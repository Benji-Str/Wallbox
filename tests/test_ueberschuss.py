"""Netzbezug ist kein Ueberschuss.

Gefunden beim Mitlesen an der echten Anlage: Die Box wurde nachts im Minuten-
takt ein- und ausgeschaltet, und im Protokoll stand "10890 W Ueberschuss",
waehrend alles aus dem Netz kam.

Die Ursache steckte in einer Zeile in `app.py`. Uebergeben wurde `feed_in_w`,
und das ist `max(0, -grid_w)` — nie negativ. Zieht die Box Strom aus dem Netz,
steht dort 0. `ChargePoint.tick` zaehlt dann den Eigenverbrauch der Box wieder
dazu (zu Recht: sonst regelte sie sich bei jedem Takt selbst weg) — und aus
11 kW Netzbezug wurde ein Ueberschuss von 11 kW.

Richtig ist der vorzeichenbehaftete Zaehlerwert: `-grid_w`.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.charge import ChargeController, ChargeConfig

W_PER_A = 690                                 # 3-phasig, 230 V
MIN_W, MAX_W = 8 * W_PER_A, 32 * W_PER_A      # 5520 .. 22080
T = {"t": 1000.0}


def mk(**kw):
    T["t"] = 1000.0
    kw.setdefault("einschalt_delay_s", 0)
    kw.setdefault("ausschalt_delay_s", 0)
    return ChargeController(ChargeConfig(**kw), MIN_W, MAX_W, W_PER_A,
                            clock=lambda: T["t"])


def ueberschuss(grid_w, lade_w, batterie_w=0.0, peer_w=0.0):
    """So rechnet app.py — und so kommt es bei ChargePoint.tick an."""
    return (-grid_w) + batterie_w - peer_w + lade_w


def zweimal(c, s_w, lade_w):
    c.tick(s_w, lade_w)
    T["t"] += 300
    return c.tick(s_w, lade_w)


print("--- Nachts aus dem Netz: kein Ueberschuss, egal wie viel gezogen wird ---")
for lade_w in (0, 5520, 11040, 22080):
    haus_w = 600
    grid = haus_w + lade_w                    # alles Bezug, keine PV
    s = zweimal(mk(mode="pv", einschalt_w=6000, ausschalt_w=0, reserve_w=150),
                ueberschuss(grid, lade_w), lade_w)
    assert not s.charging, f"{lade_w} W aus dem Netz gelten als Ueberschuss: {s.reason}"
    print(f"  Box {lade_w:5d} W, Netz {grid:6d} W -> laedt={s.charging} | {s.reason}")

print("--- Mit Sonne: der eigene Verbrauch regelt sich nicht selbst weg ---")
# 10 kW PV, 600 W Haus. Ohne Box gingen 9400 W ins Netz.
s = zweimal(mk(mode="pv", einschalt_w=6000, ausschalt_w=0, reserve_w=150),
            ueberschuss(-9400, 0), 0)
assert s.charging and s.target_w > 9000, s.reason
print(f"  Box aus, {9400} W Einspeisung -> ziel {s.target_w:.0f} W")
# Box nimmt sich 9250 W: die Einspeisung faellt auf 150 W, der Ueberschuss bleibt
s = zweimal(mk(mode="pv", einschalt_w=6000, ausschalt_w=0, reserve_w=150),
            ueberschuss(-150, 9250), 9250)
assert s.charging and s.target_w > 9000, f"regelt sich selbst weg: {s.reason}"
print(f"  Box 9250 W, Einspeisung nur noch 150 W -> ziel {s.target_w:.0f} W (stabil)")

print("--- Genau an der Grenze: Box zieht exakt den Ueberschuss ---")
s = zweimal(mk(mode="pv", einschalt_w=6000, ausschalt_w=0, reserve_w=0),
            ueberschuss(0.0, 8000), 8000)
assert s.charging and abs(s.target_w - 8000) < 1, s.reason
print(f"  Netz auf null, Box 8000 W -> ziel {s.target_w:.0f} W")

print("--- Hausspeicher, der ohnehin laedt, darf ins Auto ---")
# 3 kW gehen in den Speicher, Zaehler ausgeglichen -> die duerfen umgeleitet werden
s = zweimal(mk(mode="pv", einschalt_w=2000, ausschalt_w=0, reserve_w=0),
            ueberschuss(0.0, 0, batterie_w=6000), 0)
assert s.charging and s.target_w >= 5520, s.reason
print(f"  6000 W in den Speicher -> ziel {s.target_w:.0f} W")

print("--- Die Zeile in app.py benutzt den vorzeichenbehafteten Zaehlerwert ---")
q = (ROOT / "app.py").read_text("utf-8")
assert "-self.reading.grid_w if ok else 0.0" in q, "app.py rechnet wieder mit feed_in_w"
assert "await cp.tick(ueberschuss_w" in q
print("  app.py: ueberschuss_w = -grid_w + batterie - peer")

print("\nAlle Ueberschuss-Tests bestanden.")
