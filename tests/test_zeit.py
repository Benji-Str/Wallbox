"""Zeitladen: Zeitfenster, besonders ueber Mitternacht und mit Wochentagen."""
import sys, time, calendar
sys.path.insert(0, __file__.rsplit('/',2)[0])
from core.charge import im_fenster, naechstes_fenster, fenster_beginn, \
                        ChargeController, ChargeConfig

def ts(jahr, monat, tag, std, minute):
    return time.mktime((jahr, monat, tag, std, minute, 0, 0, 0, -1))

# Mo 2026-09-14 ... So 2026-09-20
MO, DI, FR, SA, SO = (ts(2026,9,d,0,0) for d in (14,15,18,19,20))
def am(basis, std, minute=0): return basis + std*3600 + minute*60

print("--- Normales Fenster 13:00-15:00, taeglich ---")
p = {"aktiv":True,"von":"13:00","bis":"15:00","strom_a":16}
for stunde, soll in ((12,False),(13,True),(14,True),(15,False),(16,False)):
    ist = im_fenster(p, am(MO,stunde))
    print(f"  Mo {stunde:02d}:00 -> {ist}"); assert ist == soll

print("\n--- Ueber Mitternacht 22:00-06:00, taeglich ---")
p = {"aktiv":True,"von":"22:00","bis":"06:00","strom_a":16}
for stunde, soll in ((21,False),(22,True),(23,True),(0,True),(5,True),(6,False),(12,False)):
    ist = im_fenster(p, am(MO,stunde))
    print(f"  {stunde:02d}:00 -> {ist}"); assert ist == soll, stunde

print("\n--- Ueber Mitternacht, nur Mo-Fr (Fenster gehoert zum Starttag) ---")
p = {"aktiv":True,"von":"22:00","bis":"06:00","tage":[0,1,2,3,4],"strom_a":16}
faelle = [
    (am(FR,23), True,  "Fr 23:00 — Freitagnacht gehoert dazu"),
    (am(SA,3),  True,  "Sa 03:00 — Morgenteil der Freitagnacht"),
    (am(SA,23), False, "Sa 23:00 — Samstag ist nicht dabei"),
    (am(SO,3),  False, "So 03:00 — Morgenteil der Samstagnacht"),
    (am(MO,3),  False, "Mo 03:00 — Morgenteil der Sonntagnacht"),
    (am(MO,23), True,  "Mo 23:00 — Montagnacht gehoert dazu"),
    (am(DI,3),  True,  "Di 03:00 — Morgenteil der Montagnacht"),
]
for t, soll, was in faelle:
    ist = im_fenster(p, t)
    print(f"  {was:48s} -> {ist}"); assert ist == soll, was

print("\n--- Inaktiver Plan und Unsinn ---")
assert im_fenster({"aktiv":False,"von":"00:00","bis":"23:59"}, am(MO,12)) is False
assert im_fenster({"aktiv":True,"von":"10:00","bis":"10:00"}, am(MO,10)) is False
assert im_fenster({"aktiv":True,"von":"quatsch","bis":"06:00"}, am(MO,3)) is False
print("  inaktiv / gleiche Zeit / kaputte Zeit -> alle False")

print("\n--- Mehrere Fenster, erstes passendes gewinnt ---")
plaene = [{"aktiv":True,"von":"13:00","bis":"15:00","strom_a":10},
          {"aktiv":True,"von":"22:00","bis":"06:00","strom_a":32}]
assert naechstes_fenster(plaene, am(MO,14))["strom_a"] == 10
assert naechstes_fenster(plaene, am(MO,23))["strom_a"] == 32
assert naechstes_fenster(plaene, am(MO,18)) is None
print("  14:00 -> 10 A | 23:00 -> 32 A | 18:00 -> keines")

print("\n--- Anzeige des naechsten Beginns ---")
print("  Mo 18:00 ->", fenster_beginn(plaene, am(MO,18)))
assert fenster_beginn(plaene, am(MO,18)) == "Mo 22:00"
p2 = [{"aktiv":True,"von":"08:00","bis":"10:00","tage":[5,6]}]
print("  Mo 12:00, nur Sa/So ->", fenster_beginn(p2, am(MO,12)))
assert fenster_beginn(p2, am(MO,12)) == "Sa 08:00"

print("\n--- Im Regler: laedt im Fenster, unabhaengig von der Sonne ---")
UHR = {"t": am(MO,23)}
c = ChargeController(ChargeConfig(mode="zeit", min_a=8, zeit_plaene=plaene),
                     8*690, 32*690, 690, clock=lambda: UHR["t"])
s = c.tick(0, meter_ok=False)                 # keine Sonne, kein Zaehler
print(f"  23:00 ohne Zaehler -> {s.charging} {s.target_w:.0f} W | {s.reason}")
assert s.charging and s.target_w == 32*690

UHR["t"] = am(MO,18)
s = c.tick(20000)
print(f"  18:00 mit 20 kW PV -> {s.charging} | {s.reason}")
assert not s.charging                          # ausserhalb bleibt aus

print("\n--- Fenster-Strom unter Geraete-Minimum wird angehoben ---")
c = ChargeController(ChargeConfig(mode="zeit", min_a=8,
                     zeit_plaene=[{"aktiv":True,"von":"00:00","bis":"23:59","strom_a":6}]),
                     8*690, 32*690, 690, clock=lambda: am(MO,12))
s = c.tick(0)
print(f"  6 A gewuenscht -> {s.target_w:.0f} W (= 8 A Minimum)")
assert s.target_w == 8*690

print("\nALLE TESTS OK")
