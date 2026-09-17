"""Der Mitschreiber — er darf die Box nicht anfassen.

Eine Tuya-Wallbox nimmt im lokalen Netz nur EINE Verbindung an. Ein Werkzeug,
das zum Mitlesen selbst eine aufbaut, waehrend der Dienst regelt, streitet sich
mit ihm darum — und dann scheitern Schaltbefehle, weil mitgelesen wird. Genau
das ist `dp_dump.py --watch`, und genau deshalb fragt der Mitschreiber die
eigene Schnittstelle statt das Geraet.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import schaltlog

print("--- Er redet nicht mit der Box ---")
q = (ROOT / "tools" / "schaltlog.py").read_text("utf-8")
assert "tinytuya" not in q, "der Mitschreiber darf keine zweite Verbindung aufbauen"
assert "/api/live" in q, "gefragt wird die eigene Schnittstelle"
assert "nur eine Verbindung" in q, "und er sagt auch, warum"
print("  kein tinytuya, nur /api/live")

print("--- Und braucht nichts ausser der Standardbibliothek ---")
for paket in ("httpx", "requests", "fastapi", "tinytuya"):
    assert f"import {paket}" not in q, paket
print("  laeuft mit dem System-Python, ohne .venv")

print("--- Der Control Pilot wird lesbar dargestellt ---")
assert schaltlog.fahrzeug({"cp": "controlpi_9v_pwm", "state": "paused"}).startswith("9 V+PWM")
assert schaltlog.fahrzeug({"cp": "controlpi_6v_pwm", "state": "mining"}).startswith("6 V+PWM")
assert "?" in schaltlog.fahrzeug({})
print("  9 V+PWM / 6 V+PWM / ? bei Unbekanntem")

print("--- Laufende Sperren stehen in der Zeile, Nullen nicht ---")
assert schaltlog.sperren({"sperre_s": 0, "anlauf_s": 0}) == "—"
t = schaltlog.sperren({"sperre_s": 23, "anlauf_s": 170, "nicht_geschaltet_s": 0})
assert "Takt 23s" in t and "Anlauf 170s" in t and "ohne Schuetz" not in t
print(f"  {t}")

print("--- Eine Aenderung ist eine Aenderung, ein Herunterzaehlen nicht ---")
a = {"mode": "sofort", "charging": True, "switch_on": True, "power_w": 5500,
     "amp": 8, "cp": "controlpi_6v_pwm", "state": "mining", "sperre_s": 200}
b = dict(a, sperre_s=140)                       # nur die Sperre laeuft ab
assert schaltlog.kennzeichen(a) == schaltlog.kennzeichen(b), \
    "sonst steht jede Sekunde eine Zeile da"
c = dict(a, switch_on=False)
assert schaltlog.kennzeichen(a) != schaltlog.kennzeichen(c)
d = dict(a, power_w=5520)                        # 20 W sind Rauschen
assert schaltlog.kennzeichen(a) == schaltlog.kennzeichen(d)
e = dict(a, power_w=8300)
assert schaltlog.kennzeichen(a) != schaltlog.kennzeichen(e)
print("  Sperren und Messrauschen loesen keine Zeile aus, echte Wechsel schon")

print("--- Und dp_dump warnt vor sich selbst ---")
d = (ROOT / "tools" / "dp_dump.py").read_text("utf-8")
assert "eine Verbindung" in d or "schaltlog" in d, \
    "dp_dump muss sagen, dass es dem Dienst die Verbindung wegnimmt"
print("  Hinweis vorhanden")

print("\nAlle Schaltlog-Tests bestanden.")
