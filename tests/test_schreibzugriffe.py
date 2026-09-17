"""Beim Einschalten zaehlt jeder Befehl — also so wenige wie moeglich.

An der Anlage: Mit der Karte laedt die Box, mit der Software nicht. Die Karte
setzt EINEN Datenpunkt. Die Software schickte beim Einschalten vier Befehle
ohne Pause hintereinander — Strom, Betriebsart, Strom noch einmal, Schuetz.
Tuya-Geraete nehmen schnelle Folgen ueber dieselbe Verbindung nicht
verlaesslich an; geht der letzte verloren, bleibt der Schuetz offen, und
`_set` schluckt den Fehlschlag.

Deshalb: nur schreiben, was noetig ist, mit Abstand, und ein verlorener
Schaltbefehl wird wiederholt und protokolliert.
"""
import asyncio, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import core.chargelog as CL
CL.FILE = ROOT / "chargelog_test.json"
if CL.FILE.exists():
    CL.FILE.unlink()

from core.chargepoint import ChargePoint
import drivers.tuya as WT


class FakeDrv(WT.TuyaWallbox):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.dps = {"3": "charger_insert", "4": 8, "9": 0, "14": "charge_now",
                    "18": False, "24": 40}
        self.geschrieben = []

    def _status_sync(self):
        d = dict(self.dps)
        d["9"] = d["4"] * 690 if d["18"] else 0
        return d

    def _set_sync(self, dp, v):
        self.dps[str(dp)] = v
        self.geschrieben.append((int(dp), v))
        return True


def mk(sofort_a=8, **extra):
    cfg = {"id": "wb1", "name": "Wallbox", "type": "tuya", "ip": "1.2.3.4",
           "device_id": "x", "local_key": "y", "phases": 3, "volt": 230,
           "min_a": 8, "max_a": 32, "dp_switch": 18, "dp_current": 4,
           "dp_power": 9, "dp_state": 3, "dp_temp": 24, "dp_mode": 14,
           "min_switch_interval_s": 0,
           "charge": {"mode": "sofort", "sofort_a": sofort_a}}
    cfg.update(extra)
    cp = ChargePoint(cfg, log=lambda *a: None)
    treiber = {k: v for k, v in cfg.items()
               if k not in ("id", "type", "charge", "mid_meter")}
    cp.driver.__class__ = FakeDrv
    FakeDrv.__init__(cp.driver, **treiber)
    return cp


def lauf(cp, n=1):
    for _ in range(n):
        asyncio.run(cp.tick(0.0))


print("--- Steht alles schon richtig, geht NUR der Schuetz hinaus ---")
# `get_stats` liest zuerst und merkt sich die Datenpunkte — schon im ersten
# Takt weiss der Treiber also, was ohnehin richtig steht.
cp = mk(sofort_a=8)                    # DP4 ist 8, DP14 ist charge_now
lauf(cp)
assert cp.driver.geschrieben == [(18, True)], cp.driver.geschrieben
print(f"  {cp.driver.geschrieben} — ein Befehl, wie die Karte")

print("--- Anderer Strom: zwei Befehle, und der Strom VOR dem Schuetz ---")
cp = mk(sofort_a=16)
lauf(cp)
assert cp.driver.geschrieben == [(4, 16), (18, True)], cp.driver.geschrieben
print(f"  {cp.driver.geschrieben}")

print("--- Falsche Betriebsart: dann kommt sie dazu, sonst nie ---")
cp = mk(sofort_a=8)
cp.driver.dps["14"] = "charge_pct"
lauf(cp)
assert (14, "charge_now") in cp.driver.geschrieben, cp.driver.geschrieben
assert cp.driver.geschrieben[-1] == (18, True), "der Schuetz kommt zuletzt"
print(f"  {cp.driver.geschrieben}")

print("--- Ein verlorener Schaltbefehl steht im Protokoll ---")
import contextlib, io


class TaubeDrv(FakeDrv):
    def _set_sync(self, dp, v):
        if int(dp) == 18:
            raise RuntimeError("Geraet antwortet nicht")
        return super()._set_sync(dp, v)


cp = mk(sofort_a=8)
cp.driver.__class__ = TaubeDrv
puffer = io.StringIO()
with contextlib.redirect_stdout(puffer):
    lauf(cp)
text = puffer.getvalue()
assert "FEHLGESCHLAGEN" in text, text
assert cp.driver.dps["18"] is False
print("  " + [z for z in text.splitlines() if "FEHLGESCHLAGEN" in z][0].strip())

print("--- Die LAN-Schicht haelt Abstand und versucht es zweimal ---")
class Geraet:
    def __init__(self, scheitert=0):
        self.scheitert = scheitert
        self.aufrufe = []

    def set_value(self, dp, v):
        self.aufrufe.append((dp, v, time.time()))
        if len(self.aufrufe) <= self.scheitert:
            return {"Error": "kaputt"}
        return {"dps": {str(dp): v}}


d = WT.TuyaWallbox(ip="1.2.3.4", device_id="x", local_key="y",
                   schreib_pause_s=0.2)
g = Geraet(scheitert=1)
d._dev = g
d._connect = lambda: g                 # kein Netz, nur zaehlen
t0 = time.time()
assert d._set_sync(18, True) is True, "der zweite Versuch muss greifen"
assert len(g.aufrufe) == 2, g.aufrufe
assert time.time() - t0 >= 0.2, "zwischen den Versuchen gehoert eine Pause"
print(f"  {len(g.aufrufe)} Versuche, {time.time() - t0:.2f} s mit Pause")

d2 = WT.TuyaWallbox(ip="1.2.3.4", device_id="x", local_key="y",
                    schreib_pause_s=0.0)
g2 = Geraet(scheitert=2)
d2._connect = lambda: g2
try:
    d2._set_sync(18, True)
    raise AssertionError("zwei Fehlschlaege muessen durchschlagen")
except RuntimeError:
    print("  zwei Fehlschlaege -> Fehler, kein stilles Verschlucken")

print("\nAlle Schreibzugriff-Tests bestanden.")
