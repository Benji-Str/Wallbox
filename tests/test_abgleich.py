"""Das Bild der Box darf nicht einfrieren — regelmaessiger voller Abgleich.

An der Anlage (9.11.2026, 15:06–17:03): Das Auto lud mit 5,3 kW (Tuya-App,
Hauslast), die Steuerung zeigte fast zwei Stunden lang

    work_state charger_insert · CP 9 V + PWM · 0 W · keine Phasenstroeme

und meldete „paused · 0 W" ins Portal. Ueber die dauerhaft offene Verbindung
schickt die Box nur Aenderungen; die eine Aenderung „laedt jetzt" ging verloren,
danach aenderte sich nichts mehr. Erst ein Neustart des Dienstes (frische
Verbindung) zeigte sofort 5,3 kW. Dazu kam jede Minute ein angebliches
„die Box hat selbst abgeschaltet" — und die Steuerung schaltete wieder ein.

Wächter:
  * spaetestens nach `abgleich_s` eine frische Verbindung, und deren Bild gilt,
  * ein „von selbst geschaltet" aus einer Teilmeldung wird erst nach einer
    frischen Verbindung geglaubt.
"""
import asyncio, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from drivers.tuya import TuyaWallbox

EINGESTECKT = {"1": 0, "3": "charger_insert", "4": 8, "9": 0, "10": 0,
               "13": "controlpi_9v_pwm", "14": "charge_now", "18": True, "24": 29}
LAEDT = dict(EINGESTECKT, **{"3": "charger_charging", "9": 5286,
                             "13": "controlpi_6v_pwm"})


class Box:
    """Die echte Box: frische Verbindung -> ganzes Bild; alte Verbindung ->
    nur das, was der Test als Aenderung durchlaesst (Standard: nichts)."""

    def __init__(self, d, wahr):
        self.wahr = dict(wahr)
        self.neu = True                     # die erste Verbindung ist frisch
        self.teil = []                      # Teilmeldungen der alten Verbindung
        self.verbindungen = 0
        d._status_sync = self.status
        d._drop = self.trennen

    def trennen(self):
        self.neu = True

    def status(self):
        if self.neu:
            self.neu = False
            self.verbindungen += 1
            return dict(self.wahr)
        return self.teil.pop(0) if self.teil else {}


def bauen(wahr):
    d = TuyaWallbox(ip="1.2.3.4", device_id="x", local_key="y", phases=3,
                    volt=230, min_a=8, max_a=32, schreib_pause_s=0)
    d._set_sync = lambda dp, v: True
    return d, Box(d, wahr)


def lauf(d):
    return asyncio.run(d.get_stats())


print("--- Eine verlorene Aenderung haelt hoechstens bis zum naechsten Abgleich ---")
d, box = bauen(EINGESTECKT)
st = lauf(d)
assert st.power_w == 0 and st.raw["3"] == "charger_insert"
box.wahr = LAEDT                     # das Auto laedt — die Meldung geht verloren
for _ in range(5):
    st = lauf(d)
assert st.power_w == 0, "vor dem Abgleich kann es niemand wissen"
assert d.abgleich_s <= 120, "mehr als zwei Minuten Blindflug sind zu viel"
d._abgleich_ts -= d.abgleich_s + 1   # die Zeit bis zum Abgleich ist um
st = lauf(d)
assert box.verbindungen == 2, "kein frischer Abgleich"
assert st.power_w == 5286 and st.state == "mining", (st.power_w, st.state)
assert st.raw["13"] == "controlpi_6v_pwm"
assert 0 <= st.raw["_abgleich_vor_s"] <= 1
print(f"  nach dem Abgleich: {st.power_w:.0f} W, {st.state}, {st.raw['_cp_text']}")

print("--- Ein angebliches von-selbst-aus wird erst geglaubt, wenn es stimmt ---")
d, box = bauen(LAEDT)
lauf(d)
assert d._on is True
box.teil = [{"18": False}]           # Teilmeldung sagt „aus", die Box laedt weiter
st = lauf(d)
assert box.verbindungen == 2, "nicht frisch nachgelesen"
assert d._on is True and st.power_w == 5286, "eine Teilmeldung hat die Ladung beendet"
assert not any(e["selbst"] and not e["ein"] for e in d.schaltungen), d.schaltungen
print("  frisch nachgelesen: laedt weiter, kein „selbst abgeschaltet“ im Protokoll")

print("--- Ein echtes Aus kommt durch ---")
d, box = bauen(LAEDT)
lauf(d)
box.wahr = dict(EINGESTECKT, **{"18": False})
box.teil = [{"18": False}]
st = lauf(d)
assert d._on is False and st.power_w == 0
assert d.schaltungen[-1]["selbst"] is True and d.schaltungen[-1]["ein"] is False
print("  bestaetigt: Box aus, steht so im Protokoll")

print("--- Die Karte (fremdes Ein) gilt erst nach frischem Lesen ---")
d, box = bauen(dict(EINGESTECKT, **{"18": False}))
lauf(d)
assert d._on is False
box.teil = [{"18": True}]            # Teilmeldung: an — die Box ist aber aus
st = lauf(d)
assert d._on is False and getattr(d, "karte_seit", 0) == 0, "Karte erfunden"
print("  keine erfundene Kartenladung")

print("\nAlle Abgleich-Tests bestanden.")
