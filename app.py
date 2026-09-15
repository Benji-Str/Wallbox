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
import asyncio, contextlib, json, os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
import uvicorn

from core.paths import ROOT, DATA
from core.chargepoint import ChargePoint
from core import chargelog
from meter.grid import MecMeter

WEB = ROOT / "web"
app = FastAPI(title="Wallbox-Steuerung")
ctl: "WallboxController | None" = None


class WallboxController:
    """Zaehler + Ladepunkte + Regeltakt. Mehr braucht es hier nicht."""

    def __init__(self, cfg: dict):
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
        self.chargepoints = [ChargePoint(c) for c in cfg.get("chargepoints", [])
                             if c.get("ip")]
        self.meter = MecMeter(miner_draw_cb=self._cp_draw,
                              **(cfg.get("meter") or {"mode": "mock"}))
        self.reading = None
        self._task = None

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
                ok = bool(self.reading and self.reading.ok)
                self.battery_extra_w = self._battery_extra(self.reading) if ok else 0.0
                fi = ((self.reading.feed_in_w if ok else 0.0)
                      + self.battery_extra_w - self.peer_w)
                for cp in self.chargepoints:
                    try:
                        await cp.tick(fi, ok)
                    except Exception as e:
                        print(f"[wallbox] Ladepunkt {cp.id}: {e}")
                self._publish()
            except Exception as e:
                print(f"[wallbox] {e}")
            await asyncio.sleep(self.interval_s)

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
            "chargepoints": cps,
            "total_w": round(sum(c["power_w"] for c in cps)),
            "peer_w": round(self.peer_w),
            "config_errors": self.cfg.get("_fehler") or [],
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

    def save(self):
        self.cfg["chargepoints"] = [cp.cfg for cp in self.chargepoints]
        self.cfg.pop("_fehler", None)
        try:
            (DATA / "wallbox.json").write_text(
                json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[wallbox] Config nicht gespeichert: {e}")


def _load_cfg() -> dict:
    """Eigene Config zuerst, dann eine Vorlage aus GM_CONFIG, dann das Beispiel.

    Ein Tippfehler in der eigenen Config darf den Dienst NICHT umbringen —
    sonst ist die Oberflaeche weg und man sieht nirgends, woran es lag. Statt
    dessen wird die Stelle genannt und mit der naechsten Datei weitergemacht.
    """
    fehler = []
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
        print(f"[wallbox] Konfiguration: {p}")
        cfg["_fehler"] = fehler
        return cfg
    return {"meter": {"mode": "mock"}, "chargepoints": [], "_fehler": fehler}


@app.on_event("startup")
async def _start():
    global ctl
    ctl = WallboxController(_load_cfg())
    await ctl.start()
    print(f"[wallbox] {len(ctl.chargepoints)} Ladepunkt(e), Takt {ctl.interval_s}s")


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
                           "max_total_w") if k in body}):
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
    ctl.save()
    await ctl.rebuild_meter()
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


@app.post("/api/battery")
async def api_battery(body: dict):
    """Ab welchem Ladestand das Auto die Speicher-Ladeleistung bekommt."""
    v = float(body.get("battery_release_soc", 100))
    ctl.battery_release_soc = max(0.0, min(100.0, v))
    ctl.cfg["battery_release_soc"] = ctl.battery_release_soc
    ctl.save()
    return {"battery_release_soc": ctl.battery_release_soc}


@app.get("/api/chargelog")
async def api_log(cp: str = "", limit: int = 50):
    return {"sessions": chargelog.list_sessions(cp, limit),
            "totals": chargelog.totals(cp)}


@app.get("/style.css")
async def style():
    return FileResponse(WEB / "style.css", media_type="text/css")


@app.get("/", response_class=HTMLResponse)
async def page():
    return (WEB / "index.html").read_text(encoding="utf-8")


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8081)))
