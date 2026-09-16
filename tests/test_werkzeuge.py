"""Ein Werkzeug, das in den falschen Python schickt, ist kaputt.

Aufgefallen an der echten Anlage: `python3 tools/dp_dump.py --watch` meldete
„tinytuya fehlt: pip install tinytuya". Das Paket war aber da — nur im
`.venv`, aus dem der Dienst laeuft. Wer dem Rat folgt, installiert ins System,
wo es niemand sucht, und das Werkzeug geht danach immer noch nicht.
"""
import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from core.paths import venv_hinweis

print("--- Ohne .venv: der ehrliche Ratschlag ---")
venv = ROOT / ".venv" / "bin" / "python3"
assert not venv.exists(), "dieser Test setzt eine Arbeitskopie ohne .venv voraus"
t = venv_hinweis("tinytuya")
assert "pip install tinytuya" in t and ".venv/bin/pip" in t, t
print("  " + t.splitlines()[0])

print("--- Mit .venv: der Befehl, der wirklich geht ---")
venv.parent.mkdir(parents=True, exist_ok=True)
venv.write_text("#!/bin/sh\n")
try:
    t = venv_hinweis("tinytuya")
    assert str(venv) in t, t
    assert "pip install" not in t, "nicht installieren — den richtigen Python nehmen"
    # Die eigenen Argumente muessen mitkommen, sonst tippt man sie neu
    assert " ".join(sys.argv) in t or sys.argv[0] in t, t
    print("  " + t.splitlines()[-1].strip())
finally:
    venv.unlink()
    try:
        venv.parent.rmdir(); venv.parent.parent.rmdir()
    except OSError:
        pass

print("--- Jedes Tuya-Werkzeug benutzt denselben Hinweis ---")
for name in ("dp_dump.py", "tuya_scan.py", "tuya_setup.py"):
    q = (ROOT / "tools" / name).read_text("utf-8")
    assert "venv_hinweis(" in q, name
    assert "pip install tinytuya" not in q.split('"""', 2)[-1], \
        f"{name} raet immer noch zum falschen pip"
    print(f"  {name}")

print("--- Und sie starten ueberhaupt ---")
for name in ("dp_dump.py", "tuya_scan.py", "tuya_setup.py"):
    r = subprocess.run([sys.executable, str(ROOT / "tools" / name)],
                       capture_output=True, text=True, timeout=60, input="\n")
    assert "Traceback" not in r.stderr, (name, r.stderr[-400:])
    print(f"  {name}: {(r.stdout + r.stderr).splitlines()[0][:60]}")

print("--- Eine leere Antwort ist keine Aenderung ---")
q = (ROOT / "tools" / "dp_dump.py").read_text("utf-8")
assert "if not neu:" in q and "leere Antwort" in q, \
    "eine gestoerte Messung darf nicht als Messwert durchgehen"
print("  dp_dump.py ueberspringt leere Antworten")

print("\nAlle Werkzeug-Tests bestanden.")
