"""Speicher-Vorrang: wer bekommt den Ueberschuss zuerst?"""
import sys, types
sys.path.insert(0, __file__.rsplit('/',2)[0])
from meter.grid import MeterReading

class Ctl:
    """nur die Entscheidungslogik, ohne Webserver"""
    def __init__(self, release_soc): self.battery_release_soc = float(release_soc)
    from app import WallboxController as _W
    _battery_extra = _W._battery_extra

def lese(bat=None, soc=None):
    return MeterReading(ok=True, grid_w=-1000.0, battery_w=bat, soc_pct=soc)

print("--- Speicher hat Vorrang (Voreinstellung 100 %) ---")
c = Ctl(100)
print("  laedt 2500 W bei 78 %  ->", c._battery_extra(lese(2500, 78)), "W frei")
assert c._battery_extra(lese(2500, 78)) == 0.0
print("  laedt 2500 W bei 100 % ->", c._battery_extra(lese(2500, 100)), "W frei")
assert c._battery_extra(lese(2500, 100)) == 2500.0

print("\n--- Auto ab 80 % ---")
c = Ctl(80)
print("  bei 78 % ->", c._battery_extra(lese(2500, 78)), "W")
assert c._battery_extra(lese(2500, 78)) == 0.0
print("  bei 80 % ->", c._battery_extra(lese(2500, 80)), "W")
assert c._battery_extra(lese(2500, 80)) == 2500.0

print("\n--- Auto immer zuerst (0 %) ---")
c = Ctl(0)
print("  bei 12 % ->", c._battery_extra(lese(2500, 12)), "W")
assert c._battery_extra(lese(2500, 12)) == 2500.0

print("\n--- Speicher entlaedt: da ist nichts zu holen ---")
c = Ctl(0)
print("  -1800 W  ->", c._battery_extra(lese(-1800, 50)), "W")
assert c._battery_extra(lese(-1800, 50)) == 0.0

print("\n--- Unbekannt ist nicht null ---")
c = Ctl(50)
print("  kein Speicherwert        ->", c._battery_extra(lese(None, 90)), "W")
assert c._battery_extra(lese(None, 90)) == 0.0
print("  Leistung ohne Ladestand  ->", c._battery_extra(lese(2500, None)), "W")
assert c._battery_extra(lese(2500, None)) == 0.0   # ohne SoC kein Zugriff
c = Ctl(0)
print("  ohne Ladestand, Auto=0 % ->", c._battery_extra(lese(2500, None)), "W")
assert c._battery_extra(lese(2500, None)) == 2500.0

print("\nALLE TESTS OK")
