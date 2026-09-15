import sys; sys.path.insert(0, __file__.rsplit('/',2)[0])
from core.charge import ChargeController, ChargeConfig

T = {"t": 1000.0}
clock = lambda: T["t"]
def adv(s): T["t"] += s

W_PER_A = 690            # 3-phasig
MIN_W, MAX_W = 8*690, 32*690     # 5520 .. 22080

def mk(**kw):
    T["t"] = 1000.0
    return ChargeController(ChargeConfig(**kw), MIN_W, MAX_W, W_PER_A, clock=clock)

print("--- Stop ---")
c = mk(mode="stop"); s = c.tick(20000)
print(f"  {s.charging} {s.target_w:.0f} W | {s.reason}"); assert not s.charging

print("--- Sofortladen 16 A, ohne jede PV ---")
c = mk(mode="sofort", sofort_a=16); s = c.tick(0)
print(f"  {s.charging} {s.target_w:.0f} W | {s.reason}")
assert s.charging and s.target_w == 16*690

print("--- PV: Einschaltverzoegerung ---")
c = mk(mode="pv", einschalt_w=6000, einschalt_delay_s=120, ausschalt_w=0, ausschalt_delay_s=180)
s = c.tick(7000); print(f"  t=0   {s.charging} | {s.reason}"); assert not s.charging
adv(60); s = c.tick(7000); print(f"  t=60  {s.charging} | {s.reason}"); assert not s.charging
adv(61); s = c.tick(7000); print(f"  t=121 {s.charging} {s.target_w:.0f} W | {s.reason}")
assert s.charging and s.target_w == 7000

print("--- PV: Wolke -> haelt Minimum, stoppt erst nach Verzoegerung ---")
adv(10); s = c.tick(-500); print(f"  Wolke    {s.charging} {s.target_w:.0f} W | {s.reason}")
assert s.charging and s.target_w == MIN_W
adv(100); s = c.tick(-500); print(f"  +100s    {s.charging} | {s.reason}"); assert s.charging
adv(100); s = c.tick(-500); print(f"  +200s    {s.charging} | {s.reason}"); assert not s.charging

print("--- PV: Sonne zurueck -> Verzoegerung beginnt neu ---")
adv(10); s = c.tick(9000); print(f"  sofort?  {s.charging} | {s.reason}"); assert not s.charging
adv(121); s = c.tick(9000); print(f"  +121s    {s.charging} {s.target_w:.0f} W"); assert s.charging

print("--- PV: Schwelle unter Geraete-Minimum wird angehoben ---")
c = mk(mode="pv", einschalt_w=3000, einschalt_delay_s=0, ausschalt_w=0, ausschalt_delay_s=180)
s = c.tick(4000); print(f"  4000 W -> {s.charging} | {s.reason}")
assert not s.charging                       # 4 kW reichen fuer 8 A/3ph nicht
s = c.tick(6000); print(f"  6000 W -> {s.charging} {s.target_w:.0f} W | {s.reason}")
assert s.charging and s.target_w == 6000

print("--- Min+PV: laedt auch nachts ---")
c = mk(mode="minpv", min_a=8)
s = c.tick(0); print(f"  kein Ueberschuss -> {s.target_w:.0f} W | {s.reason}")
assert s.charging and s.target_w == MIN_W
s = c.tick(15000); print(f"  15 kW Ueberschuss -> {s.target_w:.0f} W"); assert s.target_w == 15000

print("--- Deckel: mehr Ueberschuss als die Box kann ---")
s = c.tick(30000); print(f"  30 kW -> {s.target_w:.0f} W (max)"); assert s.target_w == MAX_W

print("--- Hausanschluss-Grenze 11 kW ---")
c = mk(mode="minpv", min_a=8, max_total_w=11000)
s = c.tick(30000); print(f"  30 kW -> {s.target_w:.0f} W"); assert s.target_w == 11000

print("--- Zielladen: PV bevorzugt, Netz erst wenn Zeit knapp ---")
import time as _t
lt = _t.localtime(T["t"]); 
c = mk(mode="ziel", ziel_kwh=20.0, ziel_time="%02d:%02d" % (lt.tm_hour, lt.tm_min))
s = c.tick(0, session_kwh=0)   # Zielzeit ist "jetzt+24h" -> viel Puffer
print(f"  viel Zeit, keine PV -> {s.charging} | {s.reason}"); assert not s.charging
s = c.tick(12000, session_kwh=0)
print(f"  viel Zeit, 12 kW PV -> {s.charging} {s.target_w:.0f} W"); assert s.charging
adv(86400 - 3000)              # nur noch 50 min bis Ziel
s = c.tick(0, session_kwh=0)
print(f"  Zeit knapp, keine PV -> {s.charging} {s.target_w:.0f} W | {s.reason}")
assert s.charging and s.target_w == MAX_W
s = c.tick(0, session_kwh=20.0)
print(f"  Ziel erreicht -> {s.charging} | {s.reason}"); assert not s.charging

print("\nALLE TESTS OK")
