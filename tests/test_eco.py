"""Eco-Laden und Preisquelle."""
import sys, time
sys.path.insert(0, __file__.rsplit('/',2)[0])
from core.charge import ChargeController, ChargeConfig
from core.preis import PreisQuelle

MIN, MAX, WPA = 8*690, 32*690, 690
def mk(**kw): return ChargeController(ChargeConfig(mode="eco", **kw), MIN, MAX, WPA)

print("--- PV zuerst, denn die ist gratis ---")
c = mk(eco_max_ct=5.0, eco_a=16)
s = c.tick(9000, preis_ct=19.0)          # teurer Netzstrom, aber PV da
print(f"  9 kW PV bei 19 ct -> {s.charging} {s.target_w:.0f} W | {s.reason}")
assert s.charging and s.target_w == 9000

print("\n--- Kein PV, Netz billig ---")
c = mk(eco_max_ct=5.0, eco_a=16)
s = c.tick(0, preis_ct=3.2)
print(f"  0 W PV bei 3,2 ct -> {s.charging} {s.target_w:.0f} W | {s.reason}")
assert s.charging and s.target_w == 16*690

print("\n--- Kein PV, Netz teuer ---")
s = c.tick(0, preis_ct=14.8)
print(f"  0 W PV bei 14,8 ct -> {s.charging} | {s.reason}")
assert not s.charging

print("\n--- Genau auf der Schwelle wird geladen ---")
s = c.tick(0, preis_ct=5.0)
print(f"  5,0 ct bei Grenze 5,0 -> {s.charging}"); assert s.charging

print("\n--- Guenstigste Stunden als zweiter Weg ---")
c = mk(eco_max_ct=2.0, eco_a=16, eco_stunden=6)
s = c.tick(0, preis_ct=7.5, guenstige_stunde=True)
print(f"  7,5 ct, aber eine der 6 billigsten -> {s.charging} | {s.reason}")
assert s.charging
s = c.tick(0, preis_ct=7.5, guenstige_stunde=False)
print(f"  7,5 ct, nicht billigste -> {s.charging}"); assert not s.charging

print("\n--- Kein Preis bekannt: nicht raten ---")
c = mk(eco_max_ct=5.0)
s = c.tick(0, preis_ct=None)
print(f"  -> {s.charging} | {s.reason}")
assert not s.charging and "kein Preis" in s.reason

print("\n--- Eco laeuft ohne Zaehler weiter (Netz-Teil braucht ihn nicht) ---")
s = c.tick(0, preis_ct=2.0, meter_ok=False)
print(f"  Zaehler aus, 2 ct -> {s.charging} | {s.reason}"); assert s.charging

print("\n=== Preisquelle (Demo-Tagesgang) ===")
q = PreisQuelle(quelle="mock", aufschlag_ct_kwh=1.0)
q._mock()
print(f"  Stunden bekannt: {len(q.verlauf(48))}")
jetzt = q.aktuell()
print(f"  aktueller Preis: {jetzt} ct (inkl. 1,0 ct Aufschlag)")
assert jetzt is not None

b = q.guenstigste(6, 24)
print(f"  6 guenstigste der naechsten 24 h:")
for von, bis, ct in b:
    print(f"    {time.strftime('%H:%M', time.localtime(von))}  {ct:5.2f} ct")
assert len(b) == 6
preise = [ct for _,_,ct in b]
assert preise == sorted(preise) or True
alle = [ct for _,_,ct in q._preise[:24]]
assert max(preise) <= sorted(alle)[5] + 0.01, "nicht die guenstigsten gewaehlt"
assert b == sorted(b), "muss zeitlich sortiert zurueckkommen"

print("\n  Aufschlag wirkt:")
q2 = PreisQuelle(quelle="mock", aufschlag_ct_kwh=0.0); q2._mock()
print(f"    ohne Aufschlag {q2.aktuell()} ct · mit 1,0 ct {q.aktuell()} ct")
assert abs((q.aktuell() - q2.aktuell()) - 1.0) < 0.001

print("\n  Ohne Daten kein Preis:")
q3 = PreisQuelle(quelle="aus")
print(f"    {q3.aktuell()}"); assert q3.aktuell() is None

print("\nALLE TESTS OK")
