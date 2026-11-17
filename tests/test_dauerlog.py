"""Der Dauerlog — fuenf Tage mitschreiben und danach ansehen.

Nach einer Nacht voller Einzelversuche ist das der richtige Weg: aufzeichnen,
was im Alltag passiert, und erst dann urteilen. Damit das etwas wert ist, muss
er dreierlei koennen: einen Ausfall der Steuerung ueberleben, die Platte nicht
volllaufen lassen, und die Aufzeichnung so ablegen, dass sie sich auswerten
laesst — nicht nur lesen.
"""
import contextlib, io, json, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import schaltlog

q = (ROOT / "tools" / "schaltlog.py").read_text("utf-8")

print("--- Er schreibt zwei Fassungen: eine fuer Augen, eine fuer die Auswertung ---")
# Die Textfassung wieder zu zerlegen waere bruechig — sie ist fuer Menschen
# gemacht. Deshalb daneben JSONL mit Feldern.
txt, js = schaltlog._tagesdateien("2026-09-17")
assert txt.suffix == ".txt" and js.suffix == ".jsonl"
assert txt.parent.name == "schaltlog" and txt.parent.is_dir()
print(f"  {txt.name} und {js.name} in {txt.parent.name}/")

print("--- Alte Aufzeichnungen verschwinden von selbst ---")
alt = txt.parent / "2000-01-01.jsonl"
alt.write_text("{}\n", encoding="utf-8")
import os
os.utime(alt, (0, 0))
neu = txt.parent / "heute.jsonl"
neu.write_text("{}\n", encoding="utf-8")
schaltlog._aufraeumen(30)
assert not alt.exists(), "uralte Datei blieb liegen"
assert neu.exists(), "frische Datei wurde geloescht"
schaltlog._aufraeumen(0)
assert neu.exists(), "0 heisst: nicht aufraeumen"
print("  aelter als 30 Tage weg, Frisches bleibt")

print("--- Die Auswertung findet Ladevorgaenge und ihre Schaltvorgaenge ---")
for f in txt.parent.iterdir():
    f.unlink()
t0 = time.time() - 3600
eintraege = [
    {"art": "zustand", "ts": t0, "power_w": 0, "amp": 8, "faults": []},
    {"art": "schaltung", "ts": t0 + 10, "ein": True, "ok": True, "amp": 8,
     "grund": "sofort: Sofortladen 8 A", "work_state": "charger_insert",
     "cp": "controlpi_9v_pwm"},
    {"art": "zustand", "ts": t0 + 30, "power_w": 5480, "amp": 8,
     "session_kwh": 0.1, "faults": []},
    {"art": "zustand", "ts": t0 + 1800, "power_w": 5500, "amp": 8,
     "session_kwh": 2.7, "faults": []},
    {"art": "schaltung", "ts": t0 + 1810, "ein": False, "ok": True, "amp": 8,
     "grund": "die Box hat selbst abgeschaltet", "selbst": True,
     "work_state": "charger_insert", "cp": "controlpi_9v_pwm"},
    {"art": "zustand", "ts": t0 + 2000, "power_w": 0, "amp": 8,
     "faults": ["Schuetz klebt"]},
    {"art": "schaltung", "ts": t0 + 2010, "ein": True, "ok": False, "amp": 8,
     "grund": "sofort: Sofortladen 8 A"},
]
(txt.parent / time.strftime("%Y-%m-%d", time.localtime(t0)) / "").parent.mkdir(
    parents=True, exist_ok=True)
ziel = txt.parent / (time.strftime("%Y-%m-%d", time.localtime(t0)) + ".jsonl")
ziel.write_text("\n".join(json.dumps(e) for e in eintraege), encoding="utf-8")

puffer = io.StringIO()
with contextlib.redirect_stdout(puffer):
    assert schaltlog.auswerten(5) == 0
text = puffer.getvalue()
assert "Ladevorgaenge: 1" in text, text
assert "5500 W" in text and "2.70 kWh" in text, text
assert "Schaltvorgaenge: 3" in text, text
assert "1 von der Box selbst" in text and "1 fehlgeschlagen" in text, text
assert "Schuetz klebt" in text, "gemeldete Stoerungen gehoeren in die Uebersicht"
for zeile in text.splitlines():
    if "Ladevorgaenge:" in zeile or "Schaltvorgaenge:" in zeile:
        print("  " + zeile.strip())

print("--- Ohne Aufzeichnung sagt sie, wo zu suchen ist ---")
for f in txt.parent.iterdir():
    f.unlink()
puffer = io.StringIO()
with contextlib.redirect_stdout(puffer):
    assert schaltlog.auswerten(5) == 1
assert "systemctl status wallbox-schaltlog" in puffer.getvalue()
print("  Hinweis auf den Dienst")

print("--- Der Dauerbetrieb gibt bei einem Ausfall nicht auf ---")
assert "Steuerung antwortet nicht" in q
assert "continue" in q.split("def dauerhaft")[1].split("def auswerten")[0]
assert "Restart=always" in q, "der Dienst muss sich selbst wieder starten"
assert "After=wallbox.service" in q
print("  wartet statt abzubrechen, Dienst startet neu")

print("--- Und er schaltet nichts ---")
block = q.split("def dauerhaft")[1].split("def auswerten")[0]
assert "/api/chargepoint" not in block, "der Dauerlog darf nur zusehen"
assert "tinytuya" not in q, "und die Box nicht selbst anfassen"
print("  nur lesen, kein Modus, keine Verbindung zur Box")

with contextlib.suppress(OSError):
    for f in txt.parent.iterdir():
        f.unlink()
    txt.parent.rmdir()

print("\nAlle Dauerlog-Tests bestanden.")
