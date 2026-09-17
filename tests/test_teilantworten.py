"""Tuya antwortet unvollstaendig — und das darf nicht als „aus" gelten.

Der Fehler, der die ganze Nacht gekostet hat. Im Mitschnitt an der Anlage:

    14:07:36  SCHALTUNG EIN            (erfolgreich)
    14:07:46  Schuetz aus  5346 W      (eine Sekunde spaeter angeblich aus)
    14:08:46  Schuetz aus  5346 W      (und die Leistung klebt auf dem Wert)

Der Einschaltbefehl ging durch. Aber das Geraet schickt in seinen
Statusantworten nur die Datenpunkte, die sich **geaendert** haben. Wer jede
Antwort fuer das ganze Bild nimmt, findet DP18 nicht mehr — und `dps.get("18")`
ergibt `None`, also „Schuetz aus". Die Regelung hat daraufhin endlos versucht
einzuschalten, was schon eingeschaltet war, und rannte dabei jedes Mal in den
Taktschutz.
"""
import asyncio, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from drivers.tuya import TuyaWallbox

VOLL = {"1": 63, "3": "charger_charging", "4": 8, "9": 5346, "10": 0,
        "13": "controlpi_6v_pwm", "14": "charge_now", "18": True, "24": 38}


def bauen(antworten):
    d = TuyaWallbox(ip="1.2.3.4", device_id="x", local_key="y", phases=3,
                    volt=230, min_a=8, max_a=32, schreib_pause_s=0)
    folge = list(antworten)
    d._status_sync = lambda: folge.pop(0) if folge else {}
    d.geschrieben = []

    def schreiben(dp, v):
        d.geschrieben.append((int(dp), v))
        return True

    d._set_sync = schreiben
    return d


print("--- Eine Teilantwort loescht nichts ---")
# Erst das ganze Bild, dann nur noch der Control Pilot.
d = bauen([VOLL, {"13": "controlpi_9v_pwm"}])
st = asyncio.run(d.get_stats())
assert st.raw["18"] is True and st.raw["9"] == 5346
st = asyncio.run(d.get_stats())
assert st.raw["18"] is True, "der Schuetz galt ploetzlich als aus"
assert st.raw["9"] == 5346, "die Leistung war ploetzlich unbekannt"
assert st.raw["13"] == "controlpi_9v_pwm", "das Neue muss ankommen"
assert st.raw["_frisch"] == 1, "und es steht da, wie viel frisch war"
print(f"  ein Datenpunkt neu, {len(VOLL)} im Bild, Schuetz weiter an")

print("--- Der Treiber glaubt nicht laenger, sie sei aus ---")
d = bauen([VOLL, {"24": 39}, {"24": 40}])
asyncio.run(d.get_stats())
assert d._on is True
asyncio.run(d.get_stats())
assert d._on is True, "nach einer Teilantwort galt sie als ausgeschaltet"
print("  _on bleibt True")

print("--- Ein eigener Schreibzugriff zaehlt sofort ---")
# Ohne das galt ein gerade eingeschalteter Schuetz bis zur naechsten Meldung
# des Geraets als aus — und der naechste Takt schaltete wieder ein.
d = bauen([{"3": "charger_insert", "4": 8, "9": 0, "18": False,
            "13": "controlpi_9v_pwm"}, {"24": 38}])
asyncio.run(d.get_stats())
assert d._on is False
assert asyncio.run(d.resume("Versuch")) is True
assert (18, True) in d.geschrieben
st = asyncio.run(d.get_stats())
assert st.raw["18"] is True, "das eigene Einschalten kam nicht im Bild an"
print(f"  nach resume() steht DP18 im Bild auf True, ohne Rueckmeldung")

print("--- Kommt ein Wert wirklich neu, gewinnt der neue ---")
d = bauen([VOLL, {"18": False, "9": 0, "3": "charger_insert"}])
asyncio.run(d.get_stats())
st = asyncio.run(d.get_stats())
assert st.raw["18"] is False and st.raw["9"] == 0
assert d._on is False
print("  echtes Abschalten wird nicht verschluckt")

print("--- Eine leere Antwort aendert nichts, sie loescht auch nichts ---")
# "Kein Datenpunkt hat sich geaendert" ist eine gueltige Auskunft. Das Bild
# bleibt, wie es war — vorher wurde daraus „alles unbekannt".
d = bauen([VOLL, {}])
asyncio.run(d.get_stats())
st = asyncio.run(d.get_stats())
assert st.online is True and st.raw["18"] is True and st.raw["9"] == 5346
assert st.raw["_frisch"] == 0, "und es ist erkennbar, dass nichts Neues kam"
print("  Bild unveraendert, _frisch=0")

print("--- Eine gestoerte Abfrage bleibt ein Fehler ---")
d = TuyaWallbox(ip="1.2.3.4", device_id="x", local_key="y", schreib_pause_s=0)


def kaputt():
    raise RuntimeError("keine dps in Antwort")


d._status_sync = kaputt
st = asyncio.run(d.get_stats())
assert st.online is False and st.state == "offline", (st.online, st.state)
print("  online=False, nichts wird erfunden")

print("\nAlle Teilantwort-Tests bestanden.")
