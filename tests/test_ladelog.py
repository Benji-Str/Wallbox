"""Der Versuch mit abgeschalteter Steuerung — und was er beweisen muss.

Laedt das Auto, waehrend unsere Software gar nichts anfasst, dann liegt es an
unserer Software. Laedt es auch dann nicht, liegt es nicht an ihr. Dieses
Werkzeug muss deshalb zwei Dinge sicher koennen: nichts schreiben, und
erkennen, dass der Dienst noch laeuft — eine Tuya-Box nimmt nur EINE
Verbindung an, sonst misst man den Streit und nicht die Anlage.
"""
import socket, sys, types, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

print("--- Es wird nur gelesen, nie geschrieben ---")
q = (ROOT / "tools" / "ladelog.py").read_text("utf-8")
for verboten in ("set_value", "set_status", "_set("):
    assert verboten not in q, f"{verboten} gehoert nicht in ein Lesewerkzeug"
assert "d.status()" in q
print("  kein set_value, nur status()")

import ladelog

print("--- Ein laufender Dienst wird erkannt ---")
with socket.socket() as s:
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    assert ladelog.dienst_laeuft(port) is True
assert ladelog.dienst_laeuft(port) is False      # Socket ist zu
print(f"  offener Port erkannt, geschlossener nicht")

print("--- Die Datenpunkte werden auf das Wesentliche eingedampft ---")
CFG = {"dp_switch": 18, "dp_current": 4, "dp_power": 9, "dp_state": 3,
       "dp_connection": 13, "dp_mode": 14, "dp_fault": 10}
b = ladelog.bild({"18": True, "4": 8, "9": 5480, "3": "charger_charging",
                  "13": "controlpi_6v_pwm", "14": "charge_now", "10": 0}, CFG)
assert b["an"] and b["watt"] == 5480 and b["amp"] == 8
assert b["cp_text"] == "6 V + PWM — laedt" and b["stoerungen"] == []
print(f"  {ladelog.zeile(b)}")

b2 = ladelog.bild({"18": False, "4": 8, "9": 0, "3": "charger_insert",
                   "13": "controlpi_9v_pwm", "10": 0}, CFG)
assert "Schuetz aus" in ladelog.zeile(b2) and "fordert nicht an" in ladelog.zeile(b2)
print(f"  {ladelog.zeile(b2)}")

print("--- Eine Stoerung steht im Klartext da, auch base64-kodiert ---")
# Tuya liefert Bitmaps meist als Zahl, manche Firmware base64. Unuebersetzt
# ist eine Stoerungsmeldung nichts wert — beim Bauen dieses Tests aufgefallen:
# "IA==" stand woertlich in der Ausgabe statt "Schuetz-Stoerung".
from drivers.tuya import _stoerungen
assert _stoerungen(0) == [] and _stoerungen(None) == []
assert _stoerungen(1) == ["Ueberstrom"]
assert _stoerungen(0x10) == ["Schuetz klebt"]
assert _stoerungen("IA==") == ["Schuetz-Stoerung"], _stoerungen("IA==")
assert _stoerungen("AAA=") == [], "kodierte Null ist keine Stoerung"
assert _stoerungen(0x11) == ["Ueberstrom", "Schuetz klebt"]
assert _stoerungen(1 << 20) == ["unbekanntes Bit (0x100000)"], \
    "ein unbekanntes Bit darf nicht verschwinden"
assert "unbekannte Meldung" in _stoerungen("kaputt")[0]
b3 = ladelog.bild({"18": False, "10": "IA==", "3": "charger_fault"}, CFG)
assert "Schuetz-Stoerung" in ladelog.zeile(b3), ladelog.zeile(b3)
print(f"  {', '.join(b3['stoerungen'])}  ·  Zahl, base64 und Unbekanntes geprueft")

print("--- Der Zaehlerstand ist der zweite, unabhaengige Beweis ---")
# Meldet diese Firmware DP9 nicht mit, sagt ein steigender Zaehler trotzdem,
# dass Strom fliesst. Darauf kommt es an, nicht auf einen bestimmten Datenpunkt.
b4 = ladelog.bild({"18": True, "1": 43512, "3": "charger_charging",
                   "13": "controlpi_6v_pwm"}, CFG)
assert abs(b4["kwh"] - 435.12) < 0.01, b4["kwh"]
assert "Leistung" in b4["fehlt"], b4["fehlt"]
assert "Zaehler 435.12 kWh" in ladelog.zeile(b4)
assert "OHNE: Leistung" in ladelog.zeile(b4), "fehlende Datenpunkte muessen auffallen"
print(f"  {ladelog.zeile(b4)}")

print("--- Messrauschen loest keine Zeile aus, echte Wechsel schon ---")
a = {"an": True, "zustand": "charger_charging", "cp": "controlpi_6v_pwm",
     "amp": 8.0, "modus": "charge_now", "watt": 5480.0, "kwh": 435.12,
     "stoerungen": []}
assert ladelog.kennzeichen(a) == ladelog.kennzeichen(dict(a, watt=5510.0))
assert ladelog.kennzeichen(a) != ladelog.kennzeichen(dict(a, watt=8300.0))
assert ladelog.kennzeichen(a) != ladelog.kennzeichen(dict(a, an=False))
assert ladelog.kennzeichen(a) != ladelog.kennzeichen(dict(a, cp="controlpi_9v_pwm"))
assert ladelog.kennzeichen(a) != ladelog.kennzeichen(dict(a, kwh=435.20))
print("  30 W Unterschied nein, 2800 W ja, Schuetz, CP und Zaehler immer")

print("--- Und einmal durchgespielt, mit erfundener Box ---")
class Geraet:
    def __init__(self, folge):
        self.folge, self.i = folge, 0
        self.geschrieben = []

    def set_version(self, v): pass
    def set_socketTimeout(self, v): pass
    def set_socketPersistent(self, v): pass

    def status(self):
        d = self.folge[min(self.i, len(self.folge) - 1)]
        self.i += 1
        return {"dps": d}


LAEDT = [
    {"18": False, "4": 8, "9": 0, "3": "charger_insert", "13": "controlpi_9v_pwm", "10": 0},
    {"18": True, "4": 8, "9": 0, "3": "charger_insert", "13": "controlpi_9v_pwm", "10": 0},
    {"18": True, "4": 8, "9": 5480, "3": "charger_charging", "13": "controlpi_6v_pwm", "10": 0},
]
geraet = Geraet(LAEDT)
falsches_tinytuya = types.ModuleType("tinytuya")
falsches_tinytuya.Device = lambda *a, **k: geraet
sys.modules["tinytuya"] = falsches_tinytuya
ladelog.lies_cfg = lambda: {"ip": "1.2.3.4", "device_id": "x", "local_key": "y",
                            **CFG}
ladelog.dienst_laeuft = lambda port=8081: False

import contextlib, io
puffer = io.StringIO()
with contextlib.redirect_stdout(puffer):
    ladelog.main(["--minuten", "0.12", "--takt", "1"])
text = puffer.getvalue()
assert "Das Auto HAT geladen" in text, text[-500:]
assert "an der Steuerung" in text, "das Urteil muss eindeutig sein"
assert not geraet.geschrieben
for zeile in text.splitlines():
    if "6 V + PWM" in zeile:
        print("  " + zeile.strip())
        break
print("  Urteil: " + [z for z in text.splitlines() if "Das Auto HAT" in z][0].strip())

geraet = Geraet([LAEDT[0]])
falsches_tinytuya.Device = lambda *a, **k: geraet
puffer = io.StringIO()
with contextlib.redirect_stdout(puffer):
    ladelog.main(["--minuten", "0.06", "--takt", "1"])
text = puffer.getvalue()
assert "NICHT geladen" in text and "nicht an der Software" in text, text[-400:]
print("  Urteil ohne Ladung: " + [z for z in text.splitlines() if "NICHT geladen" in z][0].strip())

print("--- Eine gestoerte Abfrage beendet den Mitschnitt nicht ---")
class Zickig:
    def __init__(self):
        self.i = 0

    def set_version(self, v): pass
    def set_socketTimeout(self, v): pass
    def set_socketPersistent(self, v): pass

    def status(self):
        self.i += 1
        if self.i == 1:
            raise OSError("Verbindung abgebrochen")
        return {"dps": {"18": True, "4": 8, "9": 5480, "1": 43512,
                        "3": "charger_charging", "13": "controlpi_6v_pwm", "10": 0}}


zick = Zickig()
falsches_tinytuya.Device = lambda *a, **k: zick
puffer = io.StringIO()
with contextlib.redirect_stdout(puffer):
    ladelog.main(["--minuten", "0.1", "--takt", "1"])
text = puffer.getvalue()
assert "Abfrage fehlgeschlagen" in text, text[-400:]
assert "Das Auto HAT geladen" in text, "danach muss weitergemessen werden"
print("  " + [z for z in text.splitlines() if "fehlgeschlagen" in z][0].strip())

print("--- Ohne DP9 entscheidet der Zaehler ---")
class NurZaehler:
    def __init__(self):
        self.i = 0

    def set_version(self, v): pass
    def set_socketTimeout(self, v): pass
    def set_socketPersistent(self, v): pass

    def status(self):
        self.i += 1
        return {"dps": {"18": True, "4": 8, "1": 43500 + self.i * 5,
                        "3": "charger_charging", "13": "controlpi_6v_pwm"}}


falsches_tinytuya.Device = lambda *a, **k: NurZaehler()
puffer = io.StringIO()
with contextlib.redirect_stdout(puffer):
    ladelog.main(["--minuten", "0.1", "--takt", "1"])
text = puffer.getvalue()
assert "meldet nicht: Leistung" in text, text[:600]
assert "Das Auto HAT geladen" in text, "der Zaehler allein muss genuegen"
print("  " + [z for z in text.splitlines() if "Zaehler der Box" in z][0].strip())

print("--- Antwortet die Box nie, sagt es das auch ---")
class Stumm:
    def set_version(self, v): pass
    def set_socketTimeout(self, v): pass
    def set_socketPersistent(self, v): pass
    def status(self): return {"Error": "kein Netz"}


falsches_tinytuya.Device = lambda *a, **k: Stumm()
puffer = io.StringIO()
with contextlib.redirect_stdout(puffer):
    ladelog.main(["--minuten", "0.06", "--takt", "1"])
text = puffer.getvalue()
assert "keine einzige Abfrage" in text and "local_key" in text, text[-400:]
print("  Hinweis auf IP, Schluessel und den laufenden Dienst")

for p in Path(ladelog.DATA).glob("ladelog-*.txt"):
    p.unlink()

print("--- Sieben Tage Kartenbetrieb: die Auswertung ---")
import json as _json
ordner = Path(ladelog.DATA) / ladelog.ORDNER
ordner.mkdir(parents=True, exist_ok=True)
for f in ordner.iterdir():
    f.unlink()
t0 = time.time() - 7200
reihe = [
    # nichts gesteckt
    {"ts": t0, "an": True, "zustand": "charger_free", "cp": "controlpi_12v",
     "amp": 8, "watt": 0, "kwh": 100.0, "stoerungen": []},
    # angesteckt, handelt aus
    {"ts": t0 + 60, "an": True, "zustand": "charger_insert",
     "cp": "controlpi_9v_pwm", "amp": 8, "watt": 0, "kwh": 100.0, "stoerungen": []},
    # laedt
    {"ts": t0 + 105, "an": True, "zustand": "charger_charging",
     "cp": "controlpi_6v_pwm", "amp": 8, "watt": 5480, "kwh": 100.0,
     "stoerungen": []},
    {"ts": t0 + 3600, "an": True, "zustand": "charger_charging",
     "cp": "controlpi_6v_pwm", "amp": 8, "watt": 5500, "kwh": 105.4,
     "stoerungen": []},
    # Box schaltet ab
    {"ts": t0 + 3700, "an": False, "zustand": "charger_insert",
     "cp": "controlpi_9v_pwm", "amp": 8, "watt": 0, "kwh": 105.4,
     "stoerungen": ["Schuetz klebt"]},
]
(ordner / time.strftime("%Y-%m-%d", time.localtime(t0))).with_suffix(".jsonl").write_text(
    "\n".join(_json.dumps(e) for e in reihe), encoding="utf-8")

puffer = io.StringIO()
with contextlib.redirect_stdout(puffer):
    assert ladelog.auswerten(7) == 0
text = puffer.getvalue()
assert "Ladevorgaenge: 1" in text, text
assert "5.40 kWh" in text, "der Zaehler liefert die Energie des Vorgangs"
assert "Schuetz-Wechsel: 1" in text and "1 x aus" in text, text
assert "Karte oder die Box selbst" in text, "ohne Steuerung gibt es keinen Dritten"
assert "Vom Anstecken bis zum Laden: 1 mal" in text, text
assert "45 s" in text, "die Aushandlungsdauer ist die interessanteste Zahl"
assert "Schuetz klebt" in text
for z in text.splitlines():
    if "Ladevorgaenge:" in z or "Anstecken bis" in z or "Schuetz-Wechsel" in z:
        print("  " + z.strip())

print("--- Der Dienst schliesst die Steuerung aus ---")
q = (ROOT / "tools" / "ladelog.py").read_text("utf-8")
assert "Conflicts=wallbox.service" in q, \
    "sonst greifen beide auf die Box zu und man misst den Streit"
assert "disable\", \"--now\", \"wallbox\"" in q.replace("'", '"'), \
    "die Steuerung muss auch nach einem Neustart aus bleiben"
assert "Restart=always" in q
assert ".venv" in q, "tinytuya liegt im venv, nicht im System-Python"
print("  Conflicts=wallbox.service, Steuerung wird abgeschaltet, venv-Python")

print("--- Auch im Dauerbetrieb wird nichts geschrieben ---")
block = q.split("def dauerhaft")[1].split("def auswerten")[0]
for verboten in ("set_value", "set_status"):
    assert verboten not in block, verboten
assert "d.status()" in block
print("  nur status(), kein einziger Schreibzugriff")

for f in ordner.iterdir():
    f.unlink()
ordner.rmdir()

print("\nAlle Ladelog-Tests bestanden.")
