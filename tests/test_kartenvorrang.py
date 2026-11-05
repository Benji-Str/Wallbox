"""Karte hat Vorrang (Betreiber 5.11.2026, ersetzt die Regel vom 16.09.).

Gemessen an der Anlage: Ohne Steuerung laedt die Box mit der Karte ganz normal.
Mit laufender Steuerung lud sie kurz und hoerte auf — oder fing gar nicht an —,
weil der naechste Regeltakt (PV ohne Ueberschuss, Stop) den per Karte gesetzten
Schalter DP18 wieder ausschaltete. Jetzt: Mit der Karte gestartet heisst laden,
bis das Fahrzeug abgesteckt wird. Ein Modus von Hand beendet den Vorrang.
"""
import asyncio, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import core.chargelog as CL
CL.FILE = ROOT / "chargelog_test_karte.json"
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
        d["3"] = "charger_charging" if d["18"] else d["3"]
        return d

    def _set_sync(self, dp, v):
        self.dps[str(dp)] = v
        self.geschrieben.append((int(dp), v))
        return True


def mk(mode):
    cfg = {"id": "wb1", "name": "Wallbox", "type": "tuya", "ip": "1.2.3.4",
           "device_id": "x", "local_key": "y", "phases": 3, "volt": 230,
           "min_a": 8, "max_a": 32, "dp_switch": 18, "dp_current": 4,
           "dp_power": 9, "dp_state": 3, "dp_temp": 24, "dp_mode": 14,
           "min_switch_interval_s": 0, "charge": {"mode": mode, "sofort_a": 16}}
    cp = ChargePoint(cfg, log=lambda *a: None)
    cp.driver.__class__ = FakeDrv
    FakeDrv.__init__(cp.driver, ip="1.2.3.4", device_id="x", local_key="y",
                     phases=3, volt=230, min_a=8, max_a=32,
                     min_switch_interval_s=0, dp_mode=14)
    return cp


def lauf(cp, n=1, ueberschuss=0.0):
    for _ in range(n):
        asyncio.run(cp.tick(ueberschuss))


for modus in ("pv", "stop", "eco"):
    print(f"--- Modus {modus}: Karte vorgehalten, keine Sonne ---")
    cp = mk(modus)
    lauf(cp, 2)
    assert cp.driver.dps["18"] is False, "vorher aus"
    cp.driver.dps["18"] = True                 # die Karte schaltet DP18 an der Box
    cp.driver.geschrieben.clear()
    lauf(cp, 8)
    assert cp.driver.dps["18"] is True, f"{modus}: Karten-Ladung wieder abgeschaltet"
    assert not cp.driver.geschrieben, f"{modus}: Regelung hat geschrieben: {cp.driver.geschrieben}"
    assert cp.live()["karte"] is True and "Karte" in cp.state.reason
    print(f"  laedt weiter, nichts geschrieben — {cp.state.reason}")

print("--- Abstecken beendet den Vorrang, danach gilt wieder der Modus ---")
cp = mk("stop")
lauf(cp, 1)
cp.driver.dps["18"] = True
lauf(cp, 2)
assert cp.driver.karte_aktiv()
cp.driver.dps["18"] = False                    # Box schaltet beim Abstecken ab
cp.driver.dps["3"] = "charger_free"
lauf(cp, 2)
assert not cp.driver.karte_aktiv(), "nach dem Abstecken kein Vorrang mehr"
print("  Vorrang beendet")

print("--- Ein Modus von Hand beendet den Vorrang (Mensch steuert) ---")
cp = mk("pv")
lauf(cp, 1)
cp.driver.dps["18"] = True
lauf(cp, 2)
cp.set_mode("stop")
assert not cp.driver.karte_aktiv()
lauf(cp, 2)
assert cp.driver.dps["18"] is False, "Stop von Hand muss wirken"
print("  Stop von Hand schaltet ab")

print("--- Eigenes Einschalten ist keine Karte ---")
cp = mk("sofort")
lauf(cp, 3)
assert cp.driver.dps["18"] is True and not cp.driver.karte_aktiv(), "eigener Start gilt nicht als Karte"
print("  eigener Start erkannt")

print("--- Laeuft die Box schon beim Start des Dienstes: nicht abwuergen ---")
cp = mk("stop")
cp.driver.dps["18"] = True
lauf(cp, 3)
assert cp.driver.dps["18"] is True and cp.driver.karte_aktiv()
print("  laufende Ladung bleibt")

if CL.FILE.exists():
    CL.FILE.unlink()
print("\nAlle Kartenvorrang-Tests bestanden.")
