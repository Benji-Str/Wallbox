"""Startauswertung: Warum laedt ein Start mit der Karte und einer der Software nicht?

Der Waechter schreibt alles mit — aber als Wand aus Zeilen. Diese Auswertung stellt
die Einschaltvorgaenge nebeneinander und nennt je Start die Befehle davor und die
Reaktion des Fahrzeugs danach. Genau diese Gegenueberstellung hat an der Anlage
wochenlang gefehlt: Vermutet wurde viel, verglichen nichts.

Gegenprobe: Wer das Urteil nicht mehr nach Leistung faellt, wird hier rot.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.waechter import startbericht as _bericht


def startbericht(ev, jetzt=None):
    """Immer mit fester Uhr pruefen — sonst haengt das Urteil an der Laufzeit."""
    return _bericht(ev, jetzt=T + 100_000 if jetzt is None else jetzt)

T = 1_762_300_000.0


def kartenstart(t):
    """Karte: ein einziger Datenpunkt, danach laedt das Auto."""
    return [
        {"ts": t, "art": "schaltung", "ein": True, "ok": True, "selbst": True,
         "grund": "an der Box gestartet (Karte)"},
        {"ts": t + 20, "art": "box", "dp": "13", "alt": "controlpi_9v_pwm",
         "neu": "controlpi_6v_pwm"},
        {"ts": t + 25, "art": "box", "dp": "3", "alt": "charger_insert",
         "neu": "charger_charging"},
        {"ts": t + 60, "art": "box", "dp": "9", "alt": 0, "neu": 5346},
    ]


def softwarestart(t):
    """Steuerung: drei Befehle, danach nimmt das Auto nicht an, die Box schaltet ab."""
    return [
        {"ts": t - 3, "art": "befehl", "dp": "14", "wert": "charge_now", "ok": True},
        {"ts": t - 2, "art": "befehl", "dp": "4", "wert": 8, "ok": True},
        {"ts": t - 1, "art": "befehl", "dp": "18", "wert": True, "ok": True},
        {"ts": t, "art": "schaltung", "ein": True, "ok": True, "selbst": False,
         "grund": "sofort: Sofortladen 8 A"},
        {"ts": t + 30, "art": "box", "dp": "13", "alt": "controlpi_12v",
         "neu": "controlpi_9v_pwm"},
        {"ts": t + 175, "art": "schaltung", "ein": False, "ok": True, "selbst": True,
         "grund": "die Box hat selbst abgeschaltet"},
    ]


print("--- Ohne Einschaltvorgang sagt die Auswertung genau das ---")
leer = startbericht([{"ts": T, "art": "box", "dp": "9", "alt": 0, "neu": 12}])
assert "nicht eingeschaltet" in leer, leer
print("  kein Start -> kein erfundenes Urteil")

print("--- Der Kartenstart wird als geladen erkannt, mit Spitze ---")
b = startbericht(kartenstart(T))
assert "KARTE/BOX" in b and "GELADEN" in b and "5346" in b, b
assert "nichts von uns" in b, b          # die Karte schreibt keine Befehle
print("  Karte: GELADEN, Spitze 5346 W, keine eigenen Befehle")

print("--- Der Softwarestart wird als nicht angenommen erkannt ---")
b = startbericht(softwarestart(T))
assert "STEUERUNG" in b and "NICHT ANGENOMMEN" in b, b
assert "9v_pwm" in b, b
assert "AUS nach 3 min durch KARTE/BOX" in b, b
# Die Befehle davor muessen dastehen — sie sind der ganze Unterschied.
for stueck in ("Betriebsart = charge_now", "Ladestrom A = 8", "SCHALTER = True"):
    assert stueck in b, (stueck, b)
print("  Steuerung: NICHT ANGENOMMEN, Box schaltet nach 175 s ab, Befehle genannt")

print("--- Beides zusammen: die Auswertung benennt den Unterschied ---")
b = startbericht(kartenstart(T) + softwarestart(T + 600))
assert "KARTE/BOX 1 von 1 geladen" in b, b
assert "STEUERUNG 0 von 1 geladen" in b, b
assert "Die Karte laedt, die Steuerung nicht" in b, b
print("  Bilanz je Weg und der Hinweis auf die Befehle")

print("--- Laedt die Steuerung, sagt sie das auch ---")
gut = softwarestart(T)
gut = [e for e in gut if not (e["art"] == "schaltung" and not e["ein"])]
gut.append({"ts": T + 90, "art": "box", "dp": "9", "alt": 0, "neu": 7200})
b = startbericht(gut)
assert "GELADEN" in b and "steuert durch" in b, b
print("  Steuerung laedt -> 'Oberflaeche steuert durch'")

print("--- Eigenverbrauch der Box ist kein Ladevorgang ---")
klein = kartenstart(T)
klein[-1]["neu"] = 120
b = startbericht(klein)
assert "GELADEN" not in b, b
print("  120 W gelten nicht als geladen")

print("--- Ein Abbruch nach 20 min wird gefunden, nicht nur einer nach 5 ---")
# Der Fall vom 5.11. nachts: mit der Karte gestartet, laedt, bricht spaeter ab.
# Mit einem festen Fenster von 300 s fehlte genau dieses Aus im Bericht — und
# damit die einzige Zeile, die sagt, WER abgeschaltet hat.
spaet = kartenstart(T) + [
    {"ts": T + 1200, "art": "schaltung", "ein": False, "ok": True, "selbst": False,
     "grund": "pv: zu wenig Ueberschuss"},
]
b = startbericht(spaet)
assert "AUS nach 20 min durch STEUERUNG" in b, b
assert "zu wenig Ueberschuss" in b, b
print("  spaeter Abbruch steht drin, mit Verursacher und Grund")

print("--- Ein Start, der noch laeuft, bekommt kein Urteil ---")
b = startbericht(softwarestart(T)[:5], jetzt=T + 40)
assert "LAEUFT NOCH" in b, b
assert "NICHT ANGENOMMEN" not in b, b
assert "noch keiner abgeschlossen" in b, b
print("  40 s nach dem Start: LAEUFT NOCH statt eines voreiligen Urteils")

print("--- Was die Box vor dem Start gemeldet hat, steht dabei ---")
vorher = [
    {"ts": T - 300, "art": "box", "dp": "13", "alt": None, "neu": "controlpi_9v_pwm"},
    {"ts": T - 299, "art": "box", "dp": "3", "alt": None, "neu": "charger_insert"},
    {"ts": T - 298, "art": "box", "dp": "4", "alt": None, "neu": 8},
]
b = startbericht(vorher + softwarestart(T))
assert "davor:       Fahrzeug controlpi_9v_pwm" in b, b
assert "Zustand charger_insert" in b and "Ladestrom 8 A" in b, b
print("  Zustand vor dem Start wird mitgefuehrt, nicht als 'unveraendert' abgetan")

print("--- Meldet die Box gar nichts, heisst das UNKLAR, nicht 'nicht angenommen' ---")
stumm = [{"ts": T, "art": "schaltung", "ein": True, "ok": True, "selbst": True,
          "grund": "Karte"}]
b = startbericht(stumm)
assert "UNKLAR" in b, b
assert "NICHT ANGENOMMEN" not in b, b
print("  keine Meldung -> UNKLAR (unbekannt ist nicht null)")

print("--- Laedt auch die Karte nicht, zeigt die Seite dorthin, wo es dann liegt ---")
ohne = kartenstart(T)
ohne[-1]["neu"] = 0
ohne[1]["neu"] = "controlpi_9v_pwm"
ohne[2]["neu"] = "charger_insert"
b = startbericht(ohne)
assert "Auch mit der Karte wurde nicht geladen" in b, b
assert "ab- und wieder anstecken" in b, b
print("  Karte laedt nicht -> Hinweis auf Kabel und Ladefehler, nicht auf die Software")

print("\nAlle Startauswertungs-Tests bestanden.")
