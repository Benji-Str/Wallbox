"""Waechter (Betreiber 05.11.2026): alles mitschreiben, was zwischen Box und Steuerung passiert.

Die Box nimmt nur EINE Verbindung an — deshalb liest der Waechter im Dienst mit, statt
eine zweite Verbindung aufzumachen. Geprueft: Box-Aenderungen, Befehle, Schaltungen
(auch Karte), Regelungsentscheidungen landen in der Tagesdatei und als Klartext.
"""
import asyncio, sys, tempfile, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import core.chargelog as CL
CL.FILE = ROOT / "chargelog_test_waechter.json"
if CL.FILE.exists():
    CL.FILE.unlink()

from core.chargepoint import ChargePoint
from core.waechter import Waechter, als_text
import drivers.tuya as WT


class FakeDrv(WT.TuyaWallbox):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.dps = {"1": 435, "3": "charger_insert", "4": 8, "9": 0,
                    "14": "charge_now", "18": False, "24": 40}
        self.offline = False

    def _status_sync(self):
        if self.offline:
            raise OSError("keine Antwort")
        d = dict(self.dps)
        d["9"] = d["4"] * 690 if d["18"] else 0
        return d

    def _set_sync(self, dp, v):
        self.dps[str(dp)] = v
        return True


def mk(mode, ordner):
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
    cp.driver.waechter = Waechter(ordner)
    return cp


def lauf(cp, n=1):
    for _ in range(n):
        asyncio.run(cp.tick(0.0))


with tempfile.TemporaryDirectory() as d:
    ordner = Path(d) / "waechter"
    print("--- Sofortladen: Befehle, Schaltung, Box-Aenderung, Regelung ---")
    cp = mk("sofort", ordner)
    lauf(cp, 3)
    alle = cp.driver.waechter.lesen(1)
    arten = {e["art"] for e in alle}
    assert {"box", "befehl", "schaltung", "regelung"} <= arten, arten
    assert any(e["art"] == "befehl" and e["dp"] == "18" and e["wert"] is True and e["ok"] for e in alle)
    assert any(e["art"] == "box" and e["dp"] == "18" for e in alle) or \
        any(e["art"] == "schaltung" and e["ein"] for e in alle)
    print(f"  {len(alle)} Ereignisse, Arten: {sorted(arten)}")

    print("--- Karte: als Schaltung der Box und Regelung mit KARTE ---")
    cp = mk("stop", ordner)
    lauf(cp, 1)
    cp.driver.dps["18"] = True
    lauf(cp, 2)
    alle = cp.driver.waechter.lesen(1)
    assert any(e["art"] == "schaltung" and e.get("selbst") and "Karte" in e.get("grund", "") for e in alle)
    assert any(e["art"] == "regelung" and e.get("karte") for e in alle)
    txt = als_text(alle)
    assert "SCHALTUNG EIN durch KARTE/BOX" in txt and "KARTE" in txt and "SCHALTER" in txt
    print("  Karte steht drin")

    print("--- Box nicht erreichbar: einmal FEHLER, nicht jeden Takt ---")
    cp = mk("stop", ordner)
    cp.driver.offline = True
    vorher = len(cp.driver.waechter.lesen(1))
    lauf(cp, 5)
    fehler = [e for e in cp.driver.waechter.lesen(1)[vorher:] if e["art"] == "fehler"]
    assert len(fehler) == 1, fehler
    cp.driver.offline = False
    lauf(cp, 1)
    assert any("wieder erreichbar" in e.get("text", "") for e in cp.driver.waechter.lesen(1))
    print("  ein Eintrag beim Ausfall, einer bei der Rueckkehr")

    print("--- Dateien: eine Zeile JSON je Ereignis ---")
    datei = sorted(ordner.glob("*.jsonl"))[-1]
    for z in datei.read_text(encoding="utf-8").splitlines():
        json.loads(z)
    print(f"  {datei.name} gueltig")

print("--- Schreiben wirft nie (Ordner nicht beschreibbar) ---")
w = Waechter(Path("/proc/kein/ordner"))
w.schreiben("fehler", text="x")
print("  kein Absturz")

if CL.FILE.exists():
    CL.FILE.unlink()
print("\nAlle Waechter-Tests bestanden.")
