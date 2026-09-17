"""Boxen, die den Ladestrom nur beim Einschalten annehmen.

Am Geraet festgestellt: Die OS-EC01 uebernimmt einen waehrend des Ladens
geschriebenen Ladestrom nicht. Aendern geht nur ueber aus — neuer Wert —
kurz warten — wieder ein.

Das kostet jedes Mal einen Schuetzvorgang und eine Ladepause. Ungebremst
waeren das an einem Sonnentag leicht fuenfzig Schaltvorgaenge, und das waere
schlimmer als der Nutzen. Deshalb drei Grenzen, die diese Tests festhalten:
Mindestaenderung, Mindestabstand, und waehrend des Anlaufs gar nicht.
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
    """Box, die den Strom NUR beim Einschalten uebernimmt — wie die echte.

    Wichtig: „Schuetz an" und „Auto zieht" sind ZWEI Dinge. Genau diese
    Unterscheidung war bei der Fehlersuche der Kern — die Box kann freigegeben
    sein, waehrend das Fahrzeug nichts anfordert. `zieht` schaltet das um.
    """

    def __init__(self, **kw):
        super().__init__(**kw)
        self.dps = {"3": "charger_insert", "4": 8, "9": 0, "14": "charge_now",
                    "18": False, "24": 40}
        self.wirksam_a = 8                  # was tatsaechlich flieszt
        self.zieht = True                   # fordert das Fahrzeug an?
        self.geschrieben = []

    def _status_sync(self):
        d = dict(self.dps)
        laeuft = bool(d["18"]) and self.zieht
        d["9"] = self.wirksam_a * 690 if laeuft else 0
        d["3"] = "charger_charging" if laeuft else "charger_insert"
        return d

    def _set_sync(self, dp, v):
        self.geschrieben.append((int(dp), v))
        if int(dp) == 4 and self.dps["18"]:
            # DAS ist der Punkt: Im Betrieb verpufft ein neuer Ladestrom. Die
            # echte Box nimmt ihn gar nicht erst an. Wer ihn vor dem Ausschalten
            # schreibt, hat den Ladevorgang fuer nichts unterbrochen.
            return True
        self.dps[str(dp)] = v
        if int(dp) == 18 and v:             # beim Einschalten uebernimmt sie
            self.wirksam_a = self.dps["4"]
        return True


def mk(**extra):
    cfg = {"id": "wb1", "name": "Wallbox", "type": "tuya", "ip": "1.2.3.4",
           "device_id": "x", "local_key": "y", "phases": 3, "volt": 230,
           "min_a": 8, "max_a": 32, "dp_switch": 18, "dp_current": 4,
           "dp_power": 9, "dp_state": 3, "dp_temp": 24, "dp_mode": 14,
           "min_switch_interval_s": 300, "strom_neustart": True,
           "neustart_pause_s": 60, "neustart_ab_a": 2, "neustart_intervall_s": 600,
           "charge": {"mode": "sofort", "sofort_a": 8}}
    cfg.update(extra)
    cp = ChargePoint(cfg, log=lambda *a: None)
    treiber = {k: v for k, v in cfg.items()
               if k not in ("id", "type", "charge", "mid_meter")}
    cp.driver.__class__ = FakeDrv
    FakeDrv.__init__(cp.driver, **treiber)
    return cp


def lauf(cp, n=1):
    for _ in range(n):
        asyncio.run(cp.tick(0.0))


print("--- Erst laden, dann von 8 auf 12 A ---")
# Ausdruecklich MIT Erlaubnis zu unterbrechen: Diese Bloecke pruefen den
# Ablauf der Neuaushandlung selbst. Die Vorgabe ist inzwischen, eine laufende
# Ladung dafuer NICHT abzuwuergen — das steht weiter unten.
cp = mk(neustart_waehrend_ladung=True)
lauf(cp, 2)
assert cp.driver.dps["18"] is True and cp.driver.wirksam_a == 8
cp.driver._an_seit -= WT.ANLAUF_S + 1          # Anlauf ist durch
cp.set_mode("sofort", sofort_a=12)
cp.driver.geschrieben.clear()
lauf(cp)
assert cp.driver.dps["18"] is False, "zum Uebernehmen muss sie kurz aus"
assert cp.driver.dps["4"] == 12, "der Wert kommt NACH dem Ausschalten an"
reihenfolge = [dp for dp, _ in cp.driver.geschrieben if dp in (4, 18)]
assert reihenfolge[0] == 18, f"erst aus, dann aendern — war: {reihenfolge}"
print(f"  Reihenfolge {reihenfolge} -> {cp.driver.dps['4']} A, "
      f"Pause {cp.driver.neustart_pause_s} s")

print("--- Waehrend der Pause bleibt sie aus ---")
lauf(cp, 3)
assert cp.driver.dps["18"] is False, "zu frueh wieder eingeschaltet"
print("  bleibt aus")

print("--- Nach der Pause geht sie mit dem neuen Strom wieder an ---")
cp.driver._neustart_ab -= cp.driver.neustart_pause_s + 1
lauf(cp, 2)
assert cp.driver.dps["18"] is True, "nach der Pause muss sie wieder an"
assert cp.driver.wirksam_a == 12, f"der neue Strom wirkt nicht: {cp.driver.wirksam_a}"
print(f"  ein mit {cp.driver.wirksam_a} A -> {cp.live()['power_w']:.0f} W")

print("--- Eine kleine Aenderung ist keinen Neustart wert ---")
cp.driver._an_seit -= WT.ANLAUF_S + 1
cp.driver._letzte_aushandlung = 0.0
cp.set_mode("sofort", sofort_a=13)              # nur 1 A mehr
lauf(cp, 2)
assert cp.driver.dps["18"] is True, "wegen 1 A abgeschaltet"
assert cp.driver.wirksam_a == 12
print("  13 A angefordert, 12 A bleiben — kein Schuetzvorgang")

print("--- Und nicht oefter als der Mindestabstand ---")
cp.driver._letzte_aushandlung = time.time()     # eben erst ausgehandelt
cp.set_mode("sofort", sofort_a=20)
lauf(cp, 2)
assert cp.driver.dps["18"] is True and cp.driver.wirksam_a == 12
assert cp.live()["stromwechsel_s"] > 0
print(f"  20 A angefordert, naechste Aenderung in {cp.live()['stromwechsel_s']} s")

print("--- Waehrend der Anlauf laeuft, wird gar nichts ausgehandelt ---")
cp = mk()
lauf(cp, 2)
assert cp.driver.anlauf_rest_s() > 0
cp.driver._letzte_aushandlung = 0.0
cp.set_mode("sofort", sofort_a=16)
lauf(cp, 2)
assert cp.driver.dps["18"] is True, "dem Fahrzeug den Anlauf abgeschnitten"
print("  Fahrzeug handelt aus, Strom bleibt vorerst")

print("--- Ohne die Einstellung bleibt alles wie bisher ---")
cp = mk(strom_neustart=False)
lauf(cp, 2)
cp.driver._an_seit -= WT.ANLAUF_S + 1
cp.set_mode("sofort", sofort_a=16)
cp.driver.geschrieben.clear()
lauf(cp)
assert (4, 16) in cp.driver.geschrieben and cp.driver.dps["18"] is True
assert (18, False) not in cp.driver.geschrieben
print("  Strom live geschrieben, kein Schaltvorgang")

print("--- Eine LAUFENDE Ladung wird fuer Ampere nicht abgewuergt ---")
# An der Anlage passiert: Fuer den Wechsel von 8 auf 12 A wurde die Ladung
# unterbrochen — und der Cupra ging in den Ladefehler, der sich nur durch
# Ab- und Anstecken loesen laesst. Ein paar Ampere sind das nicht wert.
cp = mk()
lauf(cp, 2)
cp.driver.wirksam_a = 8                    # das Fahrzeug zieht wirklich
cp.driver._an_seit -= WT.ANLAUF_S + 1
cp.driver._letzte_aushandlung = 0.0
lauf(cp)
assert cp.driver.laedt_gerade() is True
cp.set_mode("sofort", sofort_a=16)
cp.driver.geschrieben.clear()
lauf(cp, 2)
assert cp.driver.dps["18"] is True, "die laufende Ladung wurde unterbrochen"
assert (18, False) not in cp.driver.geschrieben, cp.driver.geschrieben
assert cp.driver._target_a == 16, "der Wunsch muss gemerkt bleiben"
print(f"  16 A gewuenscht, Ladung laeuft weiter, Wunsch gemerkt "
      f"({cp.driver._target_a} A)")

print("--- Beim naechsten Einschalten gilt er dann ---")
cp.driver.zieht = False                    # das Fahrzeug fordert nicht mehr an
lauf(cp)
assert cp.driver.laedt_gerade() is False
cp.set_mode("stop")
cp.driver._last_switch -= WT.HAND_SPERRE_S      # die zehn Sekunden vergehen
lauf(cp, 2)
assert cp.driver.dps["18"] is False, "Stop muss ohne laufende Ladung greifen"
cp.driver.zieht = True
cp.set_mode("sofort", sofort_a=16)
cp.driver._last_switch -= WT.HAND_SPERRE_S
lauf(cp, 2)
assert cp.driver.dps["18"] is True and cp.driver.wirksam_a == 16, cp.driver.wirksam_a
print(f"  nach Aus/Ein wirken {cp.driver.wirksam_a} A")

print("--- Wer es ausdruecklich will, bekommt es weiterhin ---")
cp = mk(neustart_waehrend_ladung=True)
lauf(cp, 2)
cp.driver._an_seit -= WT.ANLAUF_S + 1
cp.driver._letzte_aushandlung = 0.0
lauf(cp)
cp.set_mode("sofort", sofort_a=16)
lauf(cp)
assert cp.driver.dps["18"] is False, "mit ausdruecklicher Erlaubnis darf sie"
print("  neustart_waehrend_ladung=True unterbricht wie zuvor")

print("--- Und die Oberflaeche sagt, dass ein Wechsel wartet ---")
cp = mk()
lauf(cp, 2)
cp.driver._an_seit -= WT.ANLAUF_S + 1
cp.driver._letzte_aushandlung = 0.0
lauf(cp)
cp.ctrl.cfg.sofort_a = 20                  # wie die Regelung, ohne set_mode
lauf(cp)
assert cp.live()["strom_wartet"] is True, cp.live()["strom_wartet"]
print("  strom_wartet=True")

print("\nAlle Stromwechsel-Tests bestanden.")
