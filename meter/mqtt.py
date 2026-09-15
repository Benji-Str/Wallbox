"""Zaehlerwerte per MQTT abholen (und den Ladezustand veroeffentlichen).

Damit liefert jede Anlage, die schon einen Broker bespielt, die Einspeisung:
Victron/VRM, Home Assistant, evcc, openWB, Shelly, ioBroker. Erst damit wird
PV-Ueberschussladen echt — mit dem Mock-Zaehler sind die Werte erfunden.

Die Nutzlast darf sein:
  * eine nackte Zahl            ->  `1234.5`
  * JSON mit Schluessel         ->  {"value": 1234.5}        json_key "value"
  * verschachteltes JSON        ->  {"data":{"p":1234.5}}    json_key "data.p"

Vorzeichen: Dieses Projekt rechnet mit `grid_w > 0 = Bezug`. Veroeffentlicht
dein System es umgekehrt (positiv = Einspeisung), `grid_sign: -1` setzen.

Werte veralten: Kommt zu einem Thema laenger als `stale_s` nichts, gilt der
Wert als unbrauchbar und die Regelung pausiert — besser als mit einem
eingefrorenen Zaehlerstand weiterzuregeln.
"""
from __future__ import annotations
import json, threading, time
from dataclasses import dataclass, field


def parse_payload(payload, json_key: str = "") -> float | None:
    """Nutzlast in eine Zahl verwandeln. None, wenn nichts Brauchbares drin ist."""
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    t = str(payload).strip()
    if not t:
        return None
    if json_key:
        try:
            d = json.loads(t)
        except Exception:
            return None
        for teil in json_key.split("."):          # "data.p" -> d["data"]["p"]
            if isinstance(d, dict) and teil in d:
                d = d[teil]
            else:
                return None
        try:
            return float(d)
        except (TypeError, ValueError):
            return None
    try:
        return float(t)
    except ValueError:
        pass
    # Zahl ohne json_key, aber JSON-Nutzlast: gaengige Schluessel probieren
    try:
        d = json.loads(t)
    except Exception:
        return None
    if isinstance(d, (int, float)):
        return float(d)
    if isinstance(d, dict):
        for k in ("value", "Value", "power", "p", "w", "watts", "state"):
            if k in d:
                try:
                    return float(d[k])
                except (TypeError, ValueError):
                    pass
    return None


@dataclass
class MqttConfig:
    host: str = ""
    port: int = 1883
    user: str = ""
    password: str = ""
    client_id: str = "wallbox-steuerung"
    topic_grid: str = ""         # Netzleistung  (+ Bezug / - Einspeisung)
    topic_pv: str = ""           # PV-Erzeugung
    topic_battery: str = ""      # Speicherleistung (+ laedt / - entlaedt)
    topic_soc: str = ""          # Ladestand des Speichers in %
    topic_home: str = ""         # Hausverbrauch
    topic_l1: str = ""
    topic_l2: str = ""
    topic_l3: str = ""
    json_key: str = ""
    grid_sign: float = 1.0       # -1, wenn positiv = Einspeisung bedeutet
    battery_sign: float = 1.0    # -1, wenn positiv = Entladen bedeutet
    scale: float = 1.0           # z. B. 1000, wenn in kW veroeffentlicht wird
    stale_s: int = 30
    publish_prefix: str = ""     # z. B. "wallbox" -> wallbox/power, wallbox/mode


class MqttSource:
    """Haelt eine Verbindung zum Broker und den letzten Wert je Thema."""

    def __init__(self, **kw):
        self.cfg = MqttConfig(**{k: v for k, v in kw.items()
                                 if k in MqttConfig.__dataclass_fields__})
        self._werte: dict[str, tuple[float, float]] = {}   # thema -> (wert, zeit)
        self._lock = threading.Lock()
        self._cli = None
        self.verbunden = False
        self.letzter_fehler = ""

    # ---------------------------------------------------------------- Verbindung
    def _themen(self) -> list[str]:
        c = self.cfg
        return [t for t in (c.topic_grid, c.topic_pv, c.topic_battery, c.topic_soc,
                            c.topic_home, c.topic_l1, c.topic_l2, c.topic_l3) if t]

    def start(self) -> bool:
        if self._cli is not None:
            return True
        if not self.cfg.host or not self._themen():
            self.letzter_fehler = "Broker oder Thema fehlt"
            return False
        try:
            import paho.mqtt.client as mqtt
        except ImportError:
            self.letzter_fehler = "paho-mqtt ist nicht installiert"
            return False
        try:
            cli = mqtt.Client(client_id=self.cfg.client_id, clean_session=True)
            if self.cfg.user:
                cli.username_pw_set(self.cfg.user, self.cfg.password)
            cli.on_connect = self._on_connect
            cli.on_message = self._on_message
            cli.on_disconnect = self._on_disconnect
            cli.reconnect_delay_set(min_delay=1, max_delay=30)
            cli.connect_async(self.cfg.host, int(self.cfg.port), keepalive=30)
            cli.loop_start()                  # eigener Thread, blockiert nichts
            self._cli = cli
            return True
        except Exception as e:
            self.letzter_fehler = f"{type(e).__name__}: {e}"
            return False

    def stop(self):
        if self._cli:
            try:
                self._cli.loop_stop(); self._cli.disconnect()
            except Exception:
                pass
        self._cli = None
        self.verbunden = False

    # ---------------------------------------------------------------- Rueckrufe
    def _on_connect(self, cli, userdata, flags, rc, *a):
        if rc == 0:
            self.verbunden = True
            self.letzter_fehler = ""
            for t in self._themen():
                cli.subscribe(t, qos=0)
        else:
            self.verbunden = False
            self.letzter_fehler = f"Broker weist ab (Code {rc})"

    def _on_disconnect(self, cli, userdata, rc, *a):
        self.verbunden = False

    def _on_message(self, cli, userdata, msg):
        w = parse_payload(msg.payload, self.cfg.json_key)
        if w is None:
            return
        with self._lock:
            self._werte[msg.topic] = (w, time.time())

    # ---------------------------------------------------------------- Abfrage
    def wert(self, thema: str):
        """Letzter Wert eines Themas, oder None wenn fehlend/veraltet."""
        if not thema:
            return None
        with self._lock:
            eintrag = self._werte.get(thema)
        if not eintrag:
            return None
        w, ts = eintrag
        if time.time() - ts > self.cfg.stale_s:
            return None
        return w

    def lese(self) -> dict:
        """Alle Werte, Vorzeichen und Faktor angewandt.

        ok=False, sobald die Netzleistung fehlt — ohne sie gibt es keinen
        Ueberschuss. Speicher, PV und Hausverbrauch sind freiwillig; fehlen
        sie, stehen sie auf None und die Regelung rechnet ohne sie.
        """
        c, f = self.cfg, self.scale_f
        l1, l2, l3 = (self.wert(c.topic_l1), self.wert(c.topic_l2), self.wert(c.topic_l3))
        grid = self.wert(c.topic_grid)
        if grid is None and None not in (l1, l2, l3):
            grid = l1 + l2 + l3               # aus den Phasen zusammensetzen
        if grid is None:
            return {"ok": False}
        bat = self.wert(c.topic_battery)
        soc = self.wert(c.topic_soc)          # Prozent, NICHT skalieren
        home = self.wert(c.topic_home)
        return {"ok": True,
                "grid_w": grid * f * c.grid_sign,
                "pv_w": (self.wert(c.topic_pv) or 0.0) * f,
                "battery_w": None if bat is None else bat * f * c.battery_sign,
                "soc_pct": soc,
                "home_w": None if home is None else home * f,
                "l1_w": (l1 or 0.0) * f * c.grid_sign,
                "l2_w": (l2 or 0.0) * f * c.grid_sign,
                "l3_w": (l3 or 0.0) * f * c.grid_sign}

    @property
    def scale_f(self) -> float:
        return float(self.cfg.scale or 1.0)

    # ---------------------------------------------------------------- Senden
    def publish(self, werte: dict):
        """Ladezustand veroeffentlichen, damit andere Systeme mitlesen koennen."""
        if not (self._cli and self.verbunden and self.cfg.publish_prefix):
            return
        p = self.cfg.publish_prefix.rstrip("/")
        for k, v in werte.items():
            try:
                self._cli.publish(f"{p}/{k}", str(v), qos=0, retain=True)
            except Exception:
                return

    def status(self) -> dict:
        with self._lock:
            alter = {t: round(time.time() - ts, 1) for t, (w, ts) in self._werte.items()}
        return {"host": self.cfg.host, "port": self.cfg.port,
                "verbunden": self.verbunden, "fehler": self.letzter_fehler,
                "themen": self._themen(), "alter_s": alter}


def suche(host: str, port: int = 1883, user: str = "", password: str = "",
          sekunden: float = 6.0, muster: str = "#", grenze: int = 400) -> dict:
    """Kurz alles mithoeren und auflisten, was der Broker hergibt.

    Den richtigen Themennamen kennt man vorher nicht — und Raten kostet mehr
    Zeit als einmal zuhoeren. Zurueck kommen Thema, letzte Nutzlast und, wo
    moeglich, die Zahl darin.
    """
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        return {"ok": False, "fehler": "paho-mqtt ist nicht installiert"}

    gefunden: dict[str, str] = {}
    fertig = threading.Event()

    def on_connect(cli, u, flags, rc, *a):
        if rc == 0:
            cli.subscribe(muster, qos=0)
        else:
            fertig.set()

    def on_message(cli, u, msg):
        if len(gefunden) < grenze:
            gefunden[msg.topic] = msg.payload.decode("utf-8", "replace")[:120]
        else:
            fertig.set()

    try:
        cli = mqtt.Client(client_id="wallbox-suche", clean_session=True)
        if user:
            cli.username_pw_set(user, password)
        cli.on_connect, cli.on_message = on_connect, on_message
        cli.connect(host, int(port), keepalive=20)
        cli.loop_start()
        fertig.wait(timeout=float(sekunden))
        cli.loop_stop(); cli.disconnect()
    except Exception as e:
        return {"ok": False, "fehler": f"{type(e).__name__}: {e}"}

    themen = []
    for t, nutz in sorted(gefunden.items()):
        zahl = parse_payload(nutz) 
        themen.append({"topic": t, "payload": nutz, "zahl": zahl,
                       "passt": _passt(t, zahl)})
    return {"ok": True, "anzahl": len(themen), "themen": themen}


def _passt(thema: str, zahl) -> str:
    """Grobe Zuordnung als Vorschlag — der Mensch entscheidet."""
    if zahl is None:
        return ""
    t = thema.lower()
    if any(w in t for w in ("soc", "ladestand", "batterylevel")) and 0 <= zahl <= 100:
        return "soc"
    if any(w in t for w in ("grid", "netz", "evu", "meter")):
        return "grid"
    if any(w in t for w in ("pv", "solar", "yield", "inverter", "wechselrichter")):
        return "pv"
    if any(w in t for w in ("battery", "batterie", "speicher", "akku")):
        return "battery"
    if any(w in t for w in ("home", "haus", "consumption", "verbrauch", "load")):
        return "home"
    return ""
