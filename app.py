"""Wallbox-Steuerung — PV-Ueberschussladen, lokal und ohne Cloud.

    python3 app.py            # -> http://localhost:8081

Liest den Zaehler, rechnet den Ueberschuss und faehrt die Wallbox in einem von
fuenf Lademodi (stop / sofort / pv / minpv / ziel). Optional mit MID-Zaehler
fuer die Abrechnung.

Kein Login: gedacht als Geraet im eigenen Netz, wie eine Wallbox-Oberflaeche
ueblicherweise. Nicht ins Internet stellen.

TEILEN SICH MEHRERE ANLAGEN EINEN ZAEHLER (z. B. Wallbox und eine andere
steuerbare Last), duerfen sie nicht unabhaengig nach demselben Ueberschuss
greifen — das schaukelt sich auf. Dafuer gibt es zwei Haken:
  * `/api/load` meldet, wieviel diese Anlage gerade beansprucht.
  * `peer_load_url` in der Config: die Last der anderen Anlage wird vom
    eigenen Ueberschuss abgezogen.
Wer abzieht und wer meldet, entscheidet die Config — genau eine Seite zieht ab.
"""
from __future__ import annotations
import asyncio, contextlib, json, os, time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
import uvicorn

from core.paths import ROOT, DATA, uebernehmen
from core.chargepoint import ChargePoint
from core.verlauf import Verlauf
from core.historie import Historie
from core import chargelog
from core.akku import lerne as akku_lernen
from meter.grid import MecMeter
from core.preis import PreisQuelle

WEB = ROOT / "web"
app = FastAPI(title="Wallbox-Steuerung")
ctl: "WallboxController | None" = None


class WallboxController:
    """Zaehler + Ladepunkte + Regeltakt. Mehr braucht es hier nicht."""

    def __init__(self, cfg: dict):
        # Woher die Config kam, gehoert nicht in die Config selbst — sonst
        # verschwindet die Angabe beim ersten Speichern wieder.
        self.quelle = cfg.pop("_quelle", "")
        self.cfg = cfg
        self.interval_s = int(cfg.get("interval_s", 10))
        # Last einer anderen Anlage am selben Zaehler (siehe Modulkopf)
        self.peer_url = cfg.get("peer_load_url") or ""
        self.peer_w = 0.0
        # Ab diesem Ladestand darf das Auto die Ladeleistung des Hausspeichers
        # beanspruchen. 100 = Speicher hat immer Vorrang (Voreinstellung),
        # 0 = Auto zuerst. Ohne diese Regel konkurrieren beide um denselben
        # Ueberschuss und der Speicher gewinnt immer, weil er schneller ist.
        self.battery_release_soc = float(cfg.get("battery_release_soc", 100))
        self.battery_extra_w = 0.0
        self.preis = PreisQuelle(**(cfg.get("preis") or {}))
        # Fahrzeug-Profil: Name fuer die Anzeige, Akkugroesse fuer den
        # Ladezustand und spaeter fuers Zielladen in Prozent statt kWh.
        self.fahrzeug = dict(cfg.get("fahrzeug") or {})
        self.speicherbar = self._pruefe_schreibbar()
        self.chargepoints = [ChargePoint(c) for c in cfg.get("chargepoints", [])
                             if c.get("ip")]
        self.meter = MecMeter(miner_draw_cb=self._cp_draw,
                              **(cfg.get("meter") or {"mode": "mock"}))
        self.reading = None
        # Verlauf fuer die Balken am Display. Eine Stunde bei 10 s Takt.
        self.verlauf = Verlauf(punkte=int(cfg.get("verlauf_punkte", 360)),
                               abstand_s=max(5, self.interval_s))
        # Dauerhafte Aufzeichnung: Minutenwerte auf die Platte, Tagesenergie
        # fuer immer. Ohne die waere nach jedem Neustart alles weg.
        self.historie = Historie(DATA, tage_behalten=int(cfg.get("tage_behalten", 400)),
                                 aufloesung_s=float(cfg.get("historie_takt_s", 60)))
        self._task = None

    def _pruefe_schreibbar(self) -> bool:
        """Kann die Konfiguration ueberhaupt geschrieben werden? Ein stilles
        Nein ist die schlimmste Variante — dann scheint alles zu klappen und
        nach dem Neustart ist es weg."""
        try:
            ziel = DATA / "wallbox.json"
            ziel.parent.mkdir(parents=True, exist_ok=True)
            probe = ziel.parent / ".schreibprobe"
            probe.write_text("x")
            probe.unlink()
            return True
        except Exception as e:
            print(f"[wallbox] ACHTUNG: {DATA} ist nicht beschreibbar ({e}) — "
                  f"Einstellungen ueberleben keinen Neustart!")
            return False

    def _cp_draw(self) -> float:
        """Was die Ladepunkte ziehen — der Mock-Zaehler rechnet es ein."""
        return sum(cp.stats.power_w for cp in self.chargepoints
                   if cp.stats and cp.stats.online)

    async def start(self):
        self._task = asyncio.create_task(self._loop())

    async def stop(self):
        if self._task:
            self._task.cancel()

    def _battery_extra(self, r) -> float:
        """Leistung, die gerade in den Hausspeicher geht und stattdessen ins
        Auto koennte — sofern der Speicher schon genug geladen ist.

        Ist kein Speicherwert bekannt (None), wird nichts angenommen. Das ist
        wichtig: 0 W hiesse "Speicher steht still", None heisst "weiss nicht".
        """
        if r.battery_w is None or r.battery_w <= 0:
            return 0.0
        if r.soc_pct is not None and r.soc_pct < self.battery_release_soc:
            return 0.0                      # Speicher hat noch Vorrang
        if r.soc_pct is None and self.battery_release_soc > 0:
            return 0.0                      # ohne Ladestand kein Zugriff
        return float(r.battery_w)

    async def _read_peer(self) -> float:
        """Was eine andere Anlage am selben Zaehler gerade beansprucht.
        Faellt sie aus, wird 0 angenommen — lieber laden als blockieren."""
        if not self.peer_url:
            return 0.0
        try:
            import urllib.request, json as _json
            def hole():
                with urllib.request.urlopen(self.peer_url, timeout=3) as r:
                    return _json.loads(r.read())
            d = await asyncio.to_thread(hole)
            return float(d.get("claimed_w", d.get("power_w", 0)) or 0)
        except Exception as e:
            print(f"[wallbox] Fremdlast nicht lesbar ({e}) — nehme 0 W an")
            return 0.0

    async def _loop(self):
        while True:
            try:
                self.reading = await self.meter.read()
                self.peer_w = await self._read_peer()
                await self.preis.hole()
                ok = bool(self.reading and self.reading.ok)
                self.battery_extra_w = self._battery_extra(self.reading) if ok else 0.0
                # Der Ueberschuss ist der **vorzeichenbehaftete** Zaehlerwert,
                # nicht die Einspeisung. `feed_in_w` ist max(0, -grid_w) und
                # damit nie negativ — zieht die Box Strom aus dem Netz, steht
                # dort 0, und weil `ChargePoint.tick` den Eigenverbrauch der Box
                # wieder dazuzaehlt (sonst regelte sie sich selbst weg), wurde
                # aus 11 kW Netzbezug ein "Ueberschuss" von 11 kW. Der PV-Modus
                # hat dann mitten in der Nacht munter aus dem Netz weitergeladen
                # und dazu "10890 W Ueberschuss" ins Protokoll geschrieben.
                ueberschuss_w = ((-self.reading.grid_w if ok else 0.0)
                                 + self.battery_extra_w - self.peer_w)
                preis = self.preis.aktuell()
                for cp in self.chargepoints:
                    stunden = cp.ctrl.cfg.eco_stunden
                    guenstig = (self.preis.ist_guenstige_stunde(stunden)
                                if stunden else False)
                    try:
                        await cp.tick(ueberschuss_w, ok, preis, guenstig)
                    except Exception as e:
                        print(f"[wallbox] Ladepunkt {cp.id}: {e}")
                self._merke_verlauf(ok)
                self._publish()
            except Exception as e:
                print(f"[wallbox] {e}")
            await asyncio.sleep(self.interval_s)

    def _merke_verlauf(self, ok: bool):
        r = self.reading
        online = [cp for cp in self.chargepoints if cp.stats and cp.stats.online]
        werte = {
            "pv": (r.pv_w if ok else None),
            "netz": (r.grid_w if ok else None),
            "haus": (r.home_w if ok else None),
            "soc": (r.soc_pct if ok else None),
            "akku": (r.battery_w if ok else None),
            # Die Ladeleistung kommt aus der Box selbst und ist auch dann
            # bekannt, wenn der Zaehler schweigt.
            "laden": (sum(cp.stats.power_w for cp in online) if online else None),
        }
        self.verlauf.merke(**werte)
        self.historie.merke(**werte)

    def _publish(self):
        """Ladezustand auf den Broker legen, damit andere Systeme mitlesen."""
        m = getattr(self.meter, "_mqtt", None)
        if not m:
            return
        cps = [cp.live() for cp in self.chargepoints]
        if not cps:
            return
        c = cps[0]
        m.publish({"power": round(c["power_w"]), "target": round(c["target_w"]),
                   "mode": c["mode"], "charging": int(bool(c["charging"])),
                   "plugged": int(bool(c["plugged"])), "state": c["state"],
                   "session_kwh": c["session_kwh"]})
        self._melde_bus(m, c)

    # Woerter fuer die Anzeige im Hauptsystem — dort steht kein Fachbegriff,
    # sondern was gerade los ist.
    _ZUSTAND = {"stop": "aus", "sofort": "Sofortladen", "pv": "PV-Ueberschuss",
                "minpv": "Min + PV", "ziel": "Zielladen", "zeit": "Zeitladen",
                "eco": "Eco"}

    def _melde_bus(self, m, c: dict):
        """Eigenen Zustand im Vertrag des Hauptsystems melden.

        GridMine ist das Hauptsystem, diese Steuerung eine Erweiterung davon.
        Sie meldet ihren Zustand auf `gridmine/wallbox/<id>/status`; das
        Hauptsystem zeigt ihn an, ohne diese Software kennen zu muessen.
        Kommt hier nichts an, laeuft das Laden trotzdem weiter — der Bus ist
        Anzeige, keine Steuerung.
        """
        prefix = str(self.cfg.get("bus_prefix", "gridmine")).strip("/")
        if not prefix:
            return
        # Erreichbarkeit zuerst: Ist die Box nicht erreichbar, wissen wir
        # NICHTS ueber sie — auch nicht, ob ein Fahrzeug steckt. "angesteckt"
        # zu melden, weil der letzte bekannte Wert das sagte, waere geraten.
        zustand = ("offline" if not c["online"] else
                   "laedt" if c["charging"] else
                   "angesteckt" if c["plugged"] else "bereit")
        m.publish_json(f"{prefix}/wallbox/{c['id']}/status", {
            "art": "wallbox", "id": c["id"], "name": c["name"] or "Wallbox",
            "zeit": time.time(),
            "zustand": zustand,
            "text": c["reason"],
            # Die Wallbox ist ein Verbraucher: positive Leistung.
            "leistung_w": (round(c["power_w"]) if c["online"] else None),
            "gueltig_s": max(30, self.interval_s * 3),
            "kacheln": [
                {"id": "leistung", "titel": "Wallbox",
                 "wert": (round(c["power_w"]) if c["online"] else None),
                 "einheit": "W", "farbe": "blau", "rang": 30,
                 "zusatz": f"{zustand} · {self._ZUSTAND.get(c['mode'], c['mode'])}"},
                {"id": "vorgang", "titel": "Ladevorgang",
                 "wert": c["session_kwh"], "einheit": "kWh",
                 "farbe": "blau", "rang": 35,
                 "zusatz": (f"{c['amp']} A" if c["amp"] else "")},
            ],
        })

    def get(self, cpid: str):
        return next((c for c in self.chargepoints if c.id == cpid), None)

    def live(self) -> dict:
        r = self.reading
        cps = [cp.live() for cp in self.chargepoints]
        return {
            "name": self.cfg.get("name", "Wallbox"),
            "meter": {"ok": bool(r and r.ok), "mode": self.meter.mode,
                      "pv_w": round(r.pv_w) if r else 0,
                      "grid_w": round(r.grid_w) if r else 0,
                      "feed_in_w": round(r.feed_in_w) if r else 0,
                      "import_w": round(r.import_w) if r else 0,
                      "battery_w": (None if not r or r.battery_w is None
                                    else round(r.battery_w)),
                      "soc_pct": (None if not r else r.soc_pct),
                      "home_w": (None if not r or r.home_w is None
                                 else round(r.home_w))},
            "battery_extra_w": round(self.battery_extra_w),
            "battery_release_soc": self.battery_release_soc,
            "fahrzeug": {**self.fahrzeug,
                         "bild": bool(_fahrzeugbild()),
                         "bild_eigen": any(DATA.glob("fahrzeug.*")),
                         "gelernt": akku_lernen(chargelog.list_sessions("", 500))},
            "preis": self.preis.status(),
            "preis_verlauf": self.preis.verlauf(24),
            "chargepoints": cps,
            "total_w": round(sum(c["power_w"] for c in cps)),
            "peer_w": round(self.peer_w),
            "config_errors": self.cfg.get("_fehler") or [],
            "config_datei": str(DATA / "wallbox.json"),
            "config_quelle": self.quelle,
            "config_speicherbar": self.speicherbar,
            "fehlende_pakete": _fehlende_pakete(),
            "historie": self.historie.status(),
            "energie_heute": self.historie.heute(),
            "meter_cfg": {k: v for k, v in (self.cfg.get("meter") or {}).items()
                          if k != "password"},
            "mqtt": (getattr(self.meter, "_mqtt", None).status()
                     if getattr(self.meter, "_mqtt", None) else None),
        }

    async def rebuild_meter(self):
        """Zaehler nach einer Aenderung neu aufsetzen, ohne Dienst-Neustart."""
        alt = getattr(self.meter, "_mqtt", None)
        if alt:
            alt.stop()
        self.meter = MecMeter(miner_draw_cb=self._cp_draw,
                              **(self.cfg.get("meter") or {"mode": "mock"}))
        self.reading = None
        if self.meter.mode == "mqtt":
            self.meter.mqtt()          # gleich verbinden, damit die
                                       # Oberflaeche den Status sofort zeigt
        print(f"[wallbox] Zaehler neu: {self.meter.mode}")

    def save(self) -> bool:
        """Config schreiben. Rueckgabe sagt, ob es geklappt hat — ein stiller
        Fehlschlag ist das Schlimmste, was hier passieren kann."""
        self.cfg["chargepoints"] = [cp.cfg for cp in self.chargepoints]
        self.cfg.pop("_fehler", None)
        ziel = DATA / "wallbox.json"
        try:
            ziel.write_text(json.dumps(self.cfg, indent=2, ensure_ascii=False),
                            encoding="utf-8")
            return True
        except Exception as e:
            print(f"[wallbox] Config NICHT gespeichert ({ziel}): {e}")
            return False


def _load_cfg() -> dict:
    """Eigene Config zuerst, dann eine Vorlage aus GM_CONFIG, dann das Beispiel.

    Ein Tippfehler in der eigenen Config darf den Dienst NICHT umbringen —
    sonst ist die Oberflaeche weg und man sieht nirgends, woran es lag. Statt
    dessen wird die Stelle genannt und mit der naechsten Datei weitergemacht.
    """
    fehler = []
    uebernehmen("wallbox.json")
    uebernehmen("chargelog.json")
    for p in (DATA / "wallbox.json",
              Path(os.environ["GM_CONFIG"]) if os.environ.get("GM_CONFIG") else None,
              ROOT / "config.example.json"):
        if not (p and p.exists()):
            continue
        try:
            cfg = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            msg = (f"{p}: ungueltiges JSON in Zeile {e.lineno}, Spalte {e.colno} "
                   f"— {e.msg}")
            print(f"[wallbox] FEHLER {msg}")
            fehler.append(msg)
            continue
        except Exception as e:
            print(f"[wallbox] FEHLER {p}: {e}")
            fehler.append(f"{p}: {e}")
            continue
        if fehler:
            print(f"[wallbox] weiche auf {p} aus — die eigene Config ist kaputt!")
        print(f"[wallbox] Konfiguration gelesen aus: {p}")
        print(f"[wallbox] Aenderungen werden gespeichert in: {DATA / 'wallbox.json'}")
        cfg["_fehler"] = fehler
        cfg["_quelle"] = str(p)
        return cfg
    return {"meter": {"mode": "mock"}, "chargepoints": [], "_fehler": fehler}


def _fehlende_pakete() -> list:
    """Wahlfreie Abhaengigkeiten pruefen. Fehlen sie, faellt es sonst erst
    im Betrieb auf — und dann an einer Stelle, die nichts damit zu tun hat."""
    noetig = {"tinytuya": "tinytuya — OHNE DIES ERREICHT DIE STEUERUNG DIE "
                          "WALLBOX NICHT",
              "paho.mqtt.client": "paho-mqtt — Zaehlerwerte per MQTT",
              "pymodbus": "pymodbus — MID-Zaehler und Modbus-Zaehler"}
    fehlt = []
    for modul, name in noetig.items():
        try:
            __import__(modul)
        except ImportError:
            fehlt.append(name)
    return fehlt


@app.on_event("startup")
async def _start():
    global ctl
    ctl = WallboxController(_load_cfg())
    await ctl.start()
    print(f"[wallbox] {len(ctl.chargepoints)} Ladepunkt(e), Takt {ctl.interval_s}s")
    print(f"[wallbox] Konfiguration: {DATA / 'wallbox.json'} "
          f"({'beschreibbar' if ctl.speicherbar else 'NICHT BESCHREIBBAR'})")
    fehlend = _fehlende_pakete()
    if fehlend:
        print(f"[wallbox] Diese Pakete fehlen: {', '.join(fehlend)} — "
              f"nachinstallieren mit:  .venv/bin/pip install -r requirements.txt")


@app.on_event("shutdown")
async def _stop():
    with contextlib.suppress(Exception):
        await ctl.stop()


@app.get("/api/live")
async def api_live():
    return ctl.live()


@app.post("/api/chargepoint/{cpid}/mode")
async def api_mode(cpid: str, body: dict):
    cp = ctl.get(cpid)
    if not cp:
        raise HTTPException(404, "Ladepunkt unbekannt")
    if not cp.set_mode(body.get("mode", cp.ctrl.cfg.mode),
                       **{k: body.get(k) for k in
                          ("sofort_a", "min_a", "einschalt_w", "einschalt_delay_s",
                           "ausschalt_w", "ausschalt_delay_s", "ziel_kwh", "ziel_time",
                           "max_total_w", "zeit_plaene", "eco_max_ct", "eco_a",
                           "eco_stunden") if k in body}):
        raise HTTPException(400, "unbekannter Lademodus")
    ctl.save()
    return cp.live()


@app.get("/api/load")
async def api_load():
    """Fuer eine andere Anlage am selben Zaehler: was diese hier beansprucht.

    power_w   — was gerade tatsaechlich fliesst (steckt schon im Zaehlerwert)
    claimed_w — was angefordert ist; das ist der Wert zum Abziehen, denn der
                Zaehler hinkt der Anforderung einen Takt hinterher.
    """
    cps = [cp.live() for cp in ctl.chargepoints]
    return {"power_w": round(sum(c["power_w"] for c in cps)),
            "claimed_w": round(sum(c["target_w"] for c in cps)),
            "charging": any(c["charging"] for c in cps)}


@app.get("/api/meter")
async def api_meter_get():
    """Zaehler-Einstellungen. Das Passwort wird nie zurueckgegeben."""
    cfg = dict(ctl.cfg.get("meter") or {"mode": "mock"})
    cfg["password_gesetzt"] = bool(cfg.pop("password", ""))
    m = getattr(ctl.meter, "_mqtt", None)
    return {"meter": cfg, "mqtt": m.status() if m else None,
            "modes": ["mock", "mqtt", "modbus", "json"]}


@app.post("/api/meter")
async def api_meter_set(body: dict):
    """Zaehler umstellen. Ein leer gelassenes Passwort bleibt unveraendert —
    sonst wuerde jedes Speichern in der Oberflaeche es loeschen."""
    alt = dict(ctl.cfg.get("meter") or {})
    neu = {k: v for k, v in body.items() if k != "password_gesetzt"}
    if not neu.get("password") and alt.get("password"):
        neu["password"] = alt["password"]
    for zahl in ("port", "stale_s"):
        if zahl in neu and neu[zahl] != "":
            neu[zahl] = int(neu[zahl])
    for komma in ("grid_sign", "battery_sign", "scale"):
        if komma in neu and neu[komma] != "":
            neu[komma] = float(neu[komma])
    ctl.cfg["meter"] = neu
    ok = ctl.save()
    await ctl.rebuild_meter()
    # Damit im Journal steht, ob die Anfrage ankam und ob geschrieben wurde —
    # ohne das sucht man bei "wird nicht gespeichert" im Dunkeln.
    print(f"[wallbox] Zaehler gespeichert: {neu.get('mode')} "
          f"{neu.get('host','')} thema={neu.get('topic_grid','')} "
          f"-> Datei {'ok' if ok else 'FEHLGESCHLAGEN'}")
    return await api_meter_get()


@app.post("/api/mqtt/scan")
async def api_mqtt_scan(body: dict):
    """Kurz am Broker mithoeren und die Themen auflisten.

    Den richtigen Themennamen kennt man vorher nicht; einmal zuhoeren ist
    schneller als raten. Ein leer gelassenes Passwort nimmt das gespeicherte.
    """
    from meter import mqtt as _mqtt
    alt = dict(ctl.cfg.get("meter") or {})
    return await asyncio.to_thread(
        _mqtt.suche,
        body.get("host") or alt.get("host", ""),
        int(body.get("port") or alt.get("port") or 1883),
        body.get("user") or alt.get("user", ""),
        body.get("password") or alt.get("password", ""),
        min(15.0, float(body.get("sekunden") or 6)),
        body.get("muster") or "#")


@app.post("/api/mqtt/auto")
async def api_mqtt_auto(body: dict):
    """Am Broker mithoeren und je Feld Kandidaten vorschlagen.

    Vorschlagen, nicht festlegen: bei fremden Anlagen liegt jede Automatik
    manchmal daneben, und ein falsch zugeordneter Netzzaehler regelt in die
    Irre, ohne dass es auffaellt.
    """
    from meter import mqtt as _mqtt
    alt = dict(ctl.cfg.get("meter") or {})
    erg = await asyncio.to_thread(
        _mqtt.suche,
        body.get("host") or alt.get("host", ""),
        int(body.get("port") or alt.get("port") or 1883),
        body.get("user") or alt.get("user", ""),
        body.get("password") or alt.get("password", ""),
        min(15.0, float(body.get("sekunden") or 6)),
        body.get("muster") or "#")
    if not erg.get("ok"):
        return erg
    erg["vorschlag"] = _mqtt.vorschlag(erg["themen"])
    erg["felder"] = _mqtt.bester_vorschlag(erg["themen"])
    return erg


@app.get("/api/vehicle")
async def api_vehicle_get():
    return ctl.fahrzeug


@app.post("/api/vehicle")
async def api_vehicle_set(body: dict):
    """Name und Akkugroesse des Fahrzeugs."""
    neu = {"name": str(body.get("name", "")).strip()[:60]}
    try:
        kwh = float(body.get("akku_kwh") or 0)
    except (TypeError, ValueError):
        kwh = 0.0
    # 0 heisst "unbekannt" — dann bleibt der Ladebalken leer, statt einen
    # Fuellstand vorzutaeuschen.
    neu["akku_kwh"] = round(max(0.0, min(250.0, kwh)), 1)
    ctl.fahrzeug = neu
    ctl.cfg["fahrzeug"] = neu
    ctl.save()
    return neu


@app.post("/api/vehicle/akku_lernen")
async def api_akku_lernen():
    """Den gelernten Wert als Akkugroesse uebernehmen.

    Bewusst ein Knopfdruck und nicht automatisch: es ist eine Untergrenze,
    kein Messwert. Wer die Groesse aus dem Fahrzeugschein kennt, soll sie
    nicht von einer Schaetzung ueberschrieben bekommen.
    """
    e = akku_lernen(chargelog.list_sessions("", 500))
    if not e.get("kwh"):
        raise HTTPException(400, e.get("text", "noch nichts gelernt"))
    ctl.fahrzeug = {**ctl.fahrzeug, "akku_kwh": e["kwh"]}
    ctl.cfg["fahrzeug"] = ctl.fahrzeug
    ctl.save()
    return {"akku_kwh": e["kwh"], "gelernt": e}


@app.post("/api/vehicle/bild")
async def api_vehicle_bild(body: dict):
    """Foto des Fahrzeugs fuer das Dashboard, als data:-URL aus dem Browser.

    Das Bild landet in `data/`, NICHT im Repo: Pressefotos der Hersteller
    darf man fuer sich verwenden, aber nicht weiterverteilen — und dieses
    Projekt liegt oeffentlich auf GitHub.
    """
    roh = str(body.get("bild") or "")
    if not roh:
        # leer = Bild wieder entfernen, dann zeichnet die Oberflaeche wieder
        # ihr eigenes Auto
        for p in DATA.glob("fahrzeug.*"):
            p.unlink(missing_ok=True)
        return {"ok": True, "bild": False}
    kopf, _, daten = roh.partition(",")
    typen = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
    typ = next((t for t in typen if t in kopf), "")
    if not typ or "base64" not in kopf:
        raise HTTPException(400, "Nur PNG, JPEG oder WEBP")
    import base64, binascii
    try:
        bytes_ = base64.b64decode(daten, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "Bild nicht lesbar")
    if len(bytes_) > 4_000_000:
        raise HTTPException(400, "Bild ist zu gross (max. 4 MB)")
    try:
        for p in DATA.glob("fahrzeug.*"):
            p.unlink(missing_ok=True)
        DATA.mkdir(parents=True, exist_ok=True)
        (DATA / f"fahrzeug.{typen[typ]}").write_bytes(bytes_)
    except Exception as e:
        raise HTTPException(500, f"nicht gespeichert: {e}")
    return {"ok": True, "bild": True, "bytes": len(bytes_)}


def _fahrzeugbild():
    """Eigenes Foto aus dem Datenordner — sonst das mitgelieferte.

    Das mitgelieferte Bild zeigt den Kombi, fuer den diese Steuerung gebaut
    wurde. Wer eine andere Anlage hat, laedt unter Konfiguration sein eigenes
    hoch; es hat dann Vorrang.
    """
    eigen = next((p for p in sorted(DATA.glob("fahrzeug.*"))
                  if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")), None)
    if eigen:
        return eigen
    mit = WEB / "auto.png"
    return mit if mit.exists() else None


@app.get("/fahrzeug.png")
async def fahrzeugbild():
    p = _fahrzeugbild()
    if not p:
        raise HTTPException(404, "kein Bild hinterlegt")
    art = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
           ".webp": "image/webp"}[p.suffix.lower()]
    return FileResponse(p, media_type=art)


@app.post("/api/battery")
async def api_battery(body: dict):
    """Ab welchem Ladestand das Auto die Speicher-Ladeleistung bekommt."""
    v = float(body.get("battery_release_soc", 100))
    ctl.battery_release_soc = max(0.0, min(100.0, v))
    ctl.cfg["battery_release_soc"] = ctl.battery_release_soc
    ctl.save()
    return {"battery_release_soc": ctl.battery_release_soc}


@app.post("/api/chargepoint/{cpid}/device")
async def api_cp_device(cpid: str, body: dict):
    """Verbindungsdaten der Wallbox aendern — damit niemand JSON von Hand
    bearbeiten muss. Der Ladepunkt wird danach neu aufgebaut.

    Ein leer gelassener local_key bleibt erhalten; sonst wuerde jedes
    Speichern in der Oberflaeche ihn loeschen.
    """
    cp = ctl.get(cpid)
    if not cp:
        raise HTTPException(404, "Ladepunkt unbekannt")
    alt = dict(cp.cfg)
    erlaubt = ("type", "name", "ip", "device_id", "local_key", "protocol",
               "phases", "volt", "min_a", "max_a", "dp_switch", "dp_current",
               "dp_power", "dp_state", "dp_temp", "dp_mode",
               "min_switch_interval_s", "strom_neustart", "neustart_pause_s",
               "neustart_ab_a", "neustart_intervall_s")
    neu = {k: v for k, v in body.items() if k in erlaubt and v not in (None, "")}
    if not body.get("local_key") and alt.get("local_key"):
        neu["local_key"] = alt["local_key"]
    for zahl in ("phases", "volt", "min_a", "max_a", "dp_switch", "dp_current",
                 "dp_power", "dp_state", "dp_temp", "dp_mode",
                 "min_switch_interval_s", "neustart_pause_s", "neustart_ab_a",
                 "neustart_intervall_s"):
        if zahl in neu:
            neu[zahl] = int(neu[zahl])
    if "strom_neustart" in neu:
        neu["strom_neustart"] = str(neu["strom_neustart"]).lower() not in ("0", "false", "nein")
    cfg = {**alt, **neu, "id": cp.id}
    cfg["charge"] = alt.get("charge") or {}
    try:
        ersatz = ChargePoint(cfg)
    except Exception as e:
        raise HTTPException(400, f"Einstellung nicht brauchbar: {e}")
    self_idx = ctl.chargepoints.index(cp)
    ctl.chargepoints[self_idx] = ersatz
    ctl.save()
    print(f"[wallbox] Ladepunkt {cp.id} neu aufgebaut: {cfg.get('type')} {cfg.get('ip')}")
    return ersatz.live()


@app.get("/api/chargepoint/{cpid}/device")
async def api_cp_device_get(cpid: str):
    cp = ctl.get(cpid)
    if not cp:
        raise HTTPException(404, "Ladepunkt unbekannt")
    d = {k: v for k, v in cp.cfg.items() if k not in ("charge", "local_key", "mid_meter")}
    d["local_key_gesetzt"] = bool(cp.cfg.get("local_key"))
    return d


@app.get("/api/preis")
async def api_preis_get():
    return {"cfg": {"quelle": ctl.preis.cfg.quelle,
                    "aufschlag_ct_kwh": ctl.preis.cfg.aufschlag_ct_kwh},
            "status": ctl.preis.status(),
            "verlauf": ctl.preis.verlauf(36),
            "quellen": ["awattar_at", "awattar_de", "mock", "aus"]}


@app.post("/api/preis")
async def api_preis_set(body: dict):
    """Preisquelle umstellen und gleich abrufen, damit der Erfolg sichtbar ist."""
    cfg = {"quelle": body.get("quelle", "awattar_at"),
           "aufschlag_ct_kwh": float(body.get("aufschlag_ct_kwh") or 0)}
    ctl.cfg["preis"] = cfg
    ctl.preis = PreisQuelle(**cfg)
    ctl.save()
    await ctl.preis.hole(erzwingen=True)
    return await api_preis_get()


@app.post("/api/update")
async def api_update():
    """git pull im Installationsverzeichnis, dann Dienst neu starten.

    Bewusst nur ein Abholen aus dem eingerichteten Remote — es laesst sich
    also kein fremder Code einspielen, nur der Stand des eigenen Repos.
    """
    import subprocess
    ziel = str(ROOT)
    try:
        r = await asyncio.to_thread(
            subprocess.run, ["git", "-C", ziel, "pull", "--ff-only"],
            capture_output=True, text=True, timeout=120)
    except Exception as e:
        raise HTTPException(500, f"git pull nicht moeglich: {e}")
    ausgabe = (r.stdout or "") + (r.stderr or "")
    if r.returncode != 0:
        return {"ok": False, "ausgabe": ausgabe.strip()[:2000]}
    neu = "Already up to date" not in ausgabe and "Bereits aktuell" not in ausgabe
    if neu:
        # git holt nur Code. Neue Abhaengigkeiten fehlen sonst und das
        # Ergebnis ist ein "... ist nicht installiert" im Betrieb.
        pip = Path(ziel) / ".venv" / "bin" / "pip"
        if pip.exists():
            try:
                pr = await asyncio.to_thread(
                    subprocess.run, [str(pip), "install", "-q", "-r",
                                     str(Path(ziel) / "requirements.txt")],
                    capture_output=True, text=True, timeout=300)
                if pr.returncode != 0:
                    ausgabe += "\npip: " + (pr.stderr or "")[-500:]
            except Exception as e:
                ausgabe += f"\npip nicht ausgefuehrt: {e}"
        # Neustart dem Dienst ueberlassen: beenden reicht, systemd faengt es
        asyncio.get_running_loop().call_later(1.0, lambda: os._exit(0))
    return {"ok": True, "neu": neu, "ausgabe": ausgabe.strip()[:2000]}


@app.get("/api/verlauf")
async def api_verlauf(punkte: int = 40):
    """Messwerte der letzten Stunde, auf `punkte` Balken eingedampft.
    Nur im Arbeitsspeicher — nach einem Neustart faengt es wieder an."""
    return ctl.verlauf.reihen(punkte)


@app.get("/api/tag")
async def api_tag(datum: str = "", punkte: int = 288):
    """Messwerte eines Tages von der Platte. Ohne Datum: heute."""
    return ctl.historie.tag(datum, punkte)


@app.get("/api/tage")
async def api_tage(n: int = 30):
    """Tagesenergie (kWh) der letzten n Tage — das Langzeitgedaechtnis."""
    return {"tage": ctl.historie.tage(n), "heute": ctl.historie.heute(),
            "status": ctl.historie.status()}


@app.get("/api/chargelog")
async def api_log(cp: str = "", limit: int = 50):
    return {"sessions": chargelog.list_sessions(cp, limit),
            "totals": chargelog.totals(cp)}


@app.get("/ui", response_class=HTMLResponse)
@app.get("/UI", response_class=HTMLResponse)
async def handy_ui():
    """Vollbild-Ansicht fuers Handy im Querformat.

    Eigene Seite statt einer Umschaltung in der Hauptoberflaeche: quer am
    Handy soll KEIN Reiter, keine Liste und nichts zum Scrollen da sein —
    nur Zahlen und sieben grosse Schaltflaechen.
    """
    return (WEB / "ui.html").read_text(encoding="utf-8")


@app.get("/display", response_class=HTMLResponse)
async def display():
    """Grossflaechige Ansicht fuer ein fest montiertes Display neben der
    Wallbox. Bewusst eine eigene Seite: am Tablet an der Wand will man
    ablesen und antippen, nicht konfigurieren."""
    return (WEB / "display.html").read_text(encoding="utf-8")


@app.get("/style.css")
async def style():
    return FileResponse(WEB / "style.css", media_type="text/css")


@app.get("/tagesverlauf.js")
async def tagesverlauf_js():
    """Der Tagesverlauf-Graph — von der Hauptoberflaeche und vom Wanddisplay
    gemeinsam benutzt."""
    return FileResponse(WEB / "tagesverlauf.js", media_type="application/javascript")


@app.get("/spiel", response_class=HTMLResponse)
async def spiel():
    """Kleines Tipp-Spiel fuer das Wanddisplay — wer davorsteht, kann eine
    Runde spielen. Eigene Figuren, keine fremden Spielfiguren."""
    return (WEB / "spiel.html").read_text(encoding="utf-8")


@app.get("/wand", response_class=HTMLResponse)
async def wand():
    """Grossbild-Ansicht fuer ein fest montiertes Display im Hochformat
    (gedacht fuer 24 Zoll an der Wand). Zum Ansehen aus einigen Metern
    Entfernung — nichts zum Bedienen."""
    return (WEB / "wand.html").read_text(encoding="utf-8")


@app.get("/", response_class=HTMLResponse)
async def page():
    return (WEB / "index.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8081)))
