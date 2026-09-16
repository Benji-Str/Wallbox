"""Ein Fahrzeug, das gerade aushandelt, darf nicht weggeschaltet werden.

An der echten Anlage: neun Schaltvorgaenge in zehn Minuten, Control Pilot
durchgehend `9 V + PWM` — die Box gab frei, das Auto forderte nie an, geladen
wurden **0,0 kWh**. Ein Fahrzeug des VW-Konzerns braucht nach dem Einschalten
gut eine Minute, bis es von 9 V auf 6 V geht. Wer in dieser Zeit abschaltet,
bekommt nie eine Ladung zustande — und sieht trotzdem nie einen Fehler.

Deshalb: Nach dem Einschalten gilt ANLAUF_S. Die Regelung darf in diesem
Fenster drosseln, aber nicht abschalten. Ein Mensch darf es sehr wohl.
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
        self.dps = {"1": 435, "3": "charger_insert", "4": 8, "9": 0,
                    "14": "charge_now", "18": False, "24": 40}
        self.geschrieben = []

    def _status_sync(self):
        d = dict(self.dps)
        d["9"] = d["4"] * 690 if d["18"] else 0
        return d

    def _set_sync(self, dp, v):
        self.dps[str(dp)] = v
        self.geschrieben.append((int(dp), v))
        return True


def mk(mode="sofort", sperre=0):
    cfg = {"id": "wb1", "name": "Wallbox", "type": "tuya", "ip": "1.2.3.4",
           "device_id": "x", "local_key": "y", "phases": 3, "volt": 230,
           "min_a": 8, "max_a": 32, "dp_switch": 18, "dp_current": 4,
           "dp_power": 9, "dp_state": 3, "dp_temp": 24, "dp_mode": 14,
           "min_switch_interval_s": sperre,
           "charge": {"mode": mode, "sofort_a": 16}}
    cp = ChargePoint(cfg, log=lambda *a: None)
    cp.driver.__class__ = FakeDrv
    FakeDrv.__init__(cp.driver, ip="1.2.3.4", device_id="x", local_key="y",
                     phases=3, volt=230, min_a=8, max_a=32,
                     min_switch_interval_s=sperre, dp_mode=14)
    return cp


def lauf(cp, n=1):
    for _ in range(n):
        asyncio.run(cp.tick(0.0))


print("--- Die Regelung darf waehrend des Anlaufs nicht abschalten ---")
cp = mk("sofort")
lauf(cp, 2)
assert cp.driver.dps["18"] is True and cp.driver.anlauf_rest_s() > 170
cp.ctrl.cfg.mode = "stop"                 # wie die Regelung: ohne set_mode
cp.driver.geschrieben.clear()
lauf(cp, 3)
assert cp.driver.dps["18"] is True, "mitten im Anlauf weggeschaltet"
assert (4, 8) in cp.driver.geschrieben, "gedrosselt haette werden muessen"
print(f"  Schuetz bleibt an, auf {cp.driver.dps['4']} A gedrosselt, "
      f"noch {cp.driver.anlauf_rest_s():.0f} s Anlauf")

print("--- Nach dem Anlauf schaltet sie ganz normal ab ---")
cp.driver._an_seit -= WT.ANLAUF_S + 1
lauf(cp, 2)
assert cp.driver.dps["18"] is False, "nach dem Anlauf muss sie duerfen"
print("  abgeschaltet")

print("--- Ein Mensch darf trotzdem sofort stoppen ---")
cp = mk("sofort")
lauf(cp, 2)
assert cp.driver.dps["18"] is True and cp.driver.anlauf_rest_s() > 170
cp.set_mode("stop")                       # Knopf in der Oberflaeche
assert cp.driver.anlauf_rest_s() == 0, "Handbedienung hebt den Anlaufschutz auf"
lauf(cp, 2)
assert cp.driver.dps["18"] is False
print("  von Hand sofort aus")

print("--- Jeder Schaltvorgang steht mit Grund im Protokoll ---")
import io, contextlib
cp = mk("sofort")
puffer = io.StringIO()
with contextlib.redirect_stdout(puffer):
    lauf(cp, 2)
zeilen = [z for z in puffer.getvalue().splitlines() if "Schuetz" in z]
assert zeilen and "EIN" in zeilen[0] and "sofort" in zeilen[0], zeilen
print(f"  {zeilen[0].strip()}")

print("--- live() zeigt den Anlaufschutz ---")
assert cp.live()["anlauf_s"] > 0
print(f"  anlauf_s={cp.live()['anlauf_s']}")

print("\nAlle Anlaufschutz-Tests bestanden.")
