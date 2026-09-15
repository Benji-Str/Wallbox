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
    topic_home: str = ""         # Hausverbrauch (Summe)
    # Manche Anlagen veroeffentlichen nur je Phase, nie die Summe. Dann diese
    # drei setzen; die Steuerung addiert sie.
    topic_home_l1: str = ""
    topic_home_l2: str = ""
    topic_home_l3: str = ""
    topic_l1: str = ""           # Netz je Phase, falls keine Summe kommt
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
                            c.topic_home, c.topic_l1, c.topic_l2, c.topic_l3,
                            c.topic_home_l1, c.topic_home_l2, c.topic_home_l3) if t]

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

    def summe(self, *themen):
        """Phasenwerte addieren. None, sobald eine gesetzte Phase fehlt —
        eine Teilsumme waere kleiner als die Wirklichkeit und damit
        gefaehrlicher als gar kein Wert."""
        gesetzt = [t for t in themen if t]
        if not gesetzt:
            return None
        werte = [self.wert(t) for t in gesetzt]
        if any(w is None for w in werte):
            return None
        return sum(werte)

    def lese(self) -> dict:
        """Alle Werte, Vorzeichen und Faktor angewandt.

        ok=False, sobald die Netzleistung fehlt — ohne sie gibt es keinen
        Ueberschuss. Speicher, PV und Hausverbrauch sind freiwillig; fehlen
        sie, stehen sie auf None und die Regelung rechnet ohne sie.
        """
        c, f = self.cfg, self.scale_f
        l1, l2, l3 = (self.wert(c.topic_l1), self.wert(c.topic_l2), self.wert(c.topic_l3))
        grid = self.wert(c.topic_grid)
        if grid is None:
            grid = self.summe(c.topic_l1, c.topic_l2, c.topic_l3)
        if grid is None:
            return {"ok": False}
        bat = self.wert(c.topic_battery)
        soc = self.wert(c.topic_soc)          # Prozent, NICHT skalieren
        home = self.wert(c.topic_home)
        if home is None:
            home = self.summe(c.topic_home_l1, c.topic_home_l2, c.topic_home_l3)
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


def phase_aus_thema(thema: str) -> str:
    """L1/L2/L3 aus dem Themennamen raten — fuer den Zuordnungsvorschlag."""
    t = thema.lower()
    for n in ("1", "2", "3"):
        if any(m in t for m in (f"/l{n}", f"_l{n}", f"l{n}/", f"phase{n}",
                                f"phase/{n}", f"/{n}/power")):
            return "l" + n
        # Auch ohne Trennzeichen am Ende: VerbrauchL1, GridL3
        if t.endswith("l" + n):
            return "l" + n
    return ""


# ---------------------------------------------------------------- Vorschlag
#: Was offensichtlich nicht zur Energiemessung gehoert
AUSGESCHLOSSEN = ("tasmota/", "tele/", "cmnd/", "stat/", "mining/",
                  "homeassistant/", "shellies/announce")

#: (Feld, Stichworte, Vorzeichen des Gewichts)
STICHWORTE = {
    "grid":    ("grid", "netz", "evu", "em24", "zaehler", "meter"),
    "home":    ("verbrauch", "consumption", "home", "haus", "last", "load"),
    "pv":      ("pv", "solar", "inverter", "wechselrichter", "mppt", "symo",
                "yield", "erzeugung"),
    "battery": ("batt", "akku", "speicher", "laden", "entladen", "charge"),
    "soc":     ("soc", "ladestand", "batterylevel", "ladezustand"),
}

#: Einheiten im Namen, die auf die falsche Groesse hindeuten
FALSCHE_EINHEIT = (" v", "_v", "/v", " in a", " a)", "spannung", "volt",
                   "ampere", "kwh", "energie")


def _punkte(thema: str, zahl, feld: str) -> float:
    """Wie gut passt das Thema zu diesem Feld? 0 = gar nicht."""
    t = thema.lower()
    if any(t.startswith(x) or x in t for x in AUSGESCHLOSSEN):
        return 0.0
    if zahl is None:
        return 0.0
    p = 0.0
    for wort in STICHWORTE[feld]:
        if wort in t:
            p += 10.0
    if not p:
        return 0.0
    # Einheiten im Namen, die gegen eine Leistungsangabe sprechen
    if feld != "soc" and any(e in t for e in FALSCHE_EINHEIT):
        p -= 7.0
    if feld == "soc":
        # Ladestand ist ein Prozentwert; alles andere ist es nicht
        p = p + 5.0 if 0 <= zahl <= 100 else p - 8.0
    if feld in ("pv", "grid", "home") and any(w in t for w in ("total", "gesamt")):
        p += 4.0          # ein Gesamtwert ist einzelnen Straengen vorzuziehen
    if feld == "battery" and any(w in t for w in ("laden", "entladen")):
        p += 3.0          # bidirektionale Themen bevorzugen
    return p


def _spannungsverdacht(zahl, themen: list) -> bool:
    """Steht anderswo ein 'Volt'-Thema mit praktisch demselben Wert?

    Bei dieser Anlage gibt es 'Soc' = 72 und 'soc' = 59, daneben
    'Batterie V' = 59,1. Das kleine 'soc' ist offensichtlich die Spannung
    unter falschem Namen — und ein falscher Ladestand verstellt den
    Speicher-Vorrang, ohne dass man es merkt.
    """
    if zahl is None:
        return False
    for t in themen or []:
        name = str(t.get("topic", "")).lower()
        wert = t.get("zahl")
        if wert is None:
            continue
        if any(w in name for w in (" v", "_v", "/v", "volt", "spannung")):
            if abs(wert - zahl) <= max(1.0, abs(wert) * 0.02):
                return True
    return False


def vorschlag(themen: list) -> dict:
    """Aus einer Themensuche Kandidaten je Feld vorschlagen.

    Zurueck kommt je Feld eine nach Eignung sortierte Liste — entschieden
    wird in der Oberflaeche. Automatisch zuordnen heisst vorschlagen, nicht
    heimlich festlegen: bei fremden Anlagen liegt jede Automatik manchmal
    daneben, und ein falsch zugeordneter Netzzaehler regelt in die Irre.
    """
    felder = {}
    for feld in ("grid", "home", "pv", "battery", "soc"):
        einzeln, phasen = [], {"l1": [], "l2": [], "l3": []}
        for t in themen or []:
            p = _punkte(t.get("topic", ""), t.get("zahl"), feld)
            if p <= 0:
                continue
            if feld == "soc" and _spannungsverdacht(t.get("zahl"), themen):
                p -= 12.0
                if p <= 0:
                    continue
            ph = phase_aus_thema(t.get("topic", ""))
            eintrag = {"topic": t["topic"], "zahl": t.get("zahl"), "punkte": round(p, 1)}
            if ph and feld in ("grid", "home"):
                phasen[ph].append(eintrag)
            elif not ph:
                einzeln.append(eintrag)
        sortiert = lambda l: sorted(l, key=lambda x: -x["punkte"])
        felder[feld] = {"summe": sortiert(einzeln),
                        **{k: sortiert(v) for k, v in phasen.items()}}
    return felder


def bester_vorschlag(themen: list) -> dict:
    """Die jeweils beste Wahl als fertige Konfigurationsfelder."""
    v = vorschlag(themen)
    erst = lambda l: l[0]["topic"] if l else ""
    aus = {"topic_grid": "", "topic_l1": "", "topic_l2": "", "topic_l3": "",
           "topic_home": "", "topic_home_l1": "", "topic_home_l2": "",
           "topic_home_l3": "", "topic_pv": erst(v["pv"]["summe"]),
           "topic_battery": erst(v["battery"]["summe"]),
           "topic_soc": erst(v["soc"]["summe"])}
    for feld, (summe, l1, l2, l3) in (
            ("grid", ("topic_grid", "topic_l1", "topic_l2", "topic_l3")),
            ("home", ("topic_home", "topic_home_l1", "topic_home_l2", "topic_home_l3"))):
        d = v[feld]
        # Einen Summenwert nur nehmen, wenn es keine vollstaendigen Phasen
        # gibt: drei Phasen sind eindeutiger als ein Thema, das zufaellig
        # "netz" heisst.
        if d["l1"] and d["l2"] and d["l3"]:
            aus[l1], aus[l2], aus[l3] = (d["l1"][0]["topic"], d["l2"][0]["topic"],
                                         d["l3"][0]["topic"])
        else:
            aus[summe] = erst(d["summe"])
    return aus
