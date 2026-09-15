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
                fi = (self.reading.feed_in_w if self.reading.ok else 0.0) - self.peer_w
                for cp in self.chargepoints:
                    try:
                        await cp.tick(fi)
                    except Exception as e:
                        print(f"[wallbox] Ladepunkt {cp.id}: {e}")
            except Exception as e:
                print(f"[wallbox] {e}")
            await asyncio.sleep(self.interval_s)

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
                      "import_w": round(r.import_w) if r else 0},
            "chargepoints": cps,
            "total_w": round(sum(c["power_w"] for c in cps)),
            "peer_w": round(self.peer_w),
        }

    def save(self):
        self.cfg["chargepoints"] = [cp.cfg for cp in self.chargepoints]
        try:
            (DATA / "wallbox.json").write_text(
                json.dumps(self.cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            print(f"[wallbox] Config nicht gespeichert: {e}")


def _load_cfg() -> dict:
    """Eigene Config zuerst, dann eine Vorlage aus GM_CONFIG, dann die Demo."""
    for p in (DATA / "wallbox.json",
              Path(os.environ["GM_CONFIG"]) if os.environ.get("GM_CONFIG") else None,
              ROOT / "config.wallbox.json"):
        if p and p.exists():
            print(f"[wallbox] Konfiguration: {p}")
            return json.loads(p.read_text(encoding="utf-8"))
    return {"meter": {"mode": "mock"}, "chargepoints": []}


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
