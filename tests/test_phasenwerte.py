"""Die Box meldet die Phasen als base64, nicht als Zahl (gemessen 5.11.2026).

Im Waechter stand `Strom L1: None -> AAAAAAAAAAA=` und beim Laden
`CPsAHbAABtM=`. Acht Bytes: 2 Byte Spannung (0,1 V), 3 Byte Strom (mA),
3 Byte Leistung (W). Gegenprobe: 229,9 V x 7,6 A = 1747 W — und 1747 steht in
den letzten drei Bytes.

Jahrelang stand im Projektgedaechtnis, die Box melde keine Phasenstroeme. Sie
meldete sie die ganze Zeit; `float()` machte `None` daraus. Eine Zahl, die man
nicht entschluesselt, ist nicht dasselbe wie eine Zahl, die es nicht gibt.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from drivers.tuya import _phasenwert

print("--- Der gemessene Rohwert wird richtig zerlegt ---")
u, i, w = _phasenwert("CPsAHbAABtM=", 0.1)
assert (u, i, w) == (229.9, 7.6, 1747.0), (u, i, w)
assert abs(u * i - w) < 1.0, "Spannung mal Strom muss die Leistung ergeben"
print(f"  {u} V · {i} A · {w:.0f} W — und U*I = {u * i:.0f} W")

print("--- Stillstand ist null, nicht unbekannt ---")
assert _phasenwert("AAAAAAAAAAA=", 0.1) == (0.0, 0.0, 0.0)
print("  AAAAAAAAAAA= -> 0 V · 0 A · 0 W")

print("--- Eine reine Zahl bleibt Strom mal Faktor (andere Boxen) ---")
assert _phasenwert(76, 0.1) == (None, 7.6, None)
print("  76 -> 7,6 A, Spannung und Leistung unbekannt")

print("--- Unbekannt ist nicht null ---")
for murks in (None, "", "Quatsch!", "AAAA", [1, 2, 3]):
    assert _phasenwert(murks, 0.1) == (None, None, None), murks
print("  nichts, zu kurz oder unlesbar -> dreimal None, nie 0")

print("--- Im Treiber landen Strom, Spannung und Summenleistung ---")
import asyncio
import drivers.tuya as WT


class FakeDev:
    def __init__(self, dps):
        self._dps = dps

    def set_socketPersistent(self, *a, **k):
        pass

    def set_version(self, *a, **k):
        pass

    def status(self):
        return {"dps": self._dps}


d = WT.TuyaWallbox("1.2.3.4", name="WB", device_id="x", local_key="y")
d._dev = FakeDev({"18": True, "3": "charger_charging", "9": 5241,
                  "6": "CPsAHbAABtM=", "7": "CPsAHbAABtM=", "8": "CPsAHbAABtM="})
st = asyncio.get_event_loop().run_until_complete(d.get_stats())
assert st.raw["_phase_a"] == 7.6 and st.raw["_volt_a"] == 229.9, st.raw
assert st.raw["_phasen_w"] == 5241.0, st.raw["_phasen_w"]
assert st.raw["_phases_active"] == 3, st.raw["_phases_active"]
print(f"  3 Phasen à 7,6 A / 229,9 V, Summe {st.raw['_phasen_w']:.0f} W")

print("--- Meldet die Box keine Gesamtleistung, springt die Summe ein ---")
d2 = WT.TuyaWallbox("1.2.3.4", name="WB", device_id="x", local_key="y")
d2._dev = FakeDev({"18": True, "3": "charger_charging",
                   "6": "CPsAHbAABtM=", "7": "CPsAHbAABtM=", "8": "CPsAHbAABtM="})
st2 = asyncio.get_event_loop().run_until_complete(d2.get_stats())
assert st2.power_w == 5241.0, st2.power_w
print(f"  ohne DP9: power_w = {st2.power_w:.0f} W aus den Phasen gemessen")

print("--- Mit DP9 bleibt DP9 die Quelle, nicht zwei Wahrheiten ---")
assert st.power_w == 5241.0 and st.raw["_phasen_w"] == 5241.0
d3 = WT.TuyaWallbox("1.2.3.4", name="WB", device_id="x", local_key="y")
d3._dev = FakeDev({"18": True, "3": "charger_charging", "9": 4000,
                   "6": "CPsAHbAABtM=", "7": "CPsAHbAABtM=", "8": "CPsAHbAABtM="})
st3 = asyncio.get_event_loop().run_until_complete(d3.get_stats())
assert st3.power_w == 4000.0, st3.power_w
print("  DP9 schlaegt die Phasensumme")

print("\nAlle Phasenwert-Tests bestanden.")
