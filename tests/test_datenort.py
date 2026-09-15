"""Wo liegt die Konfiguration — und ueberlebt sie einen Ortswechsel?

Hintergrund: Von Hand gestartet lag sie frueher im Projektordner, als Dienst
in `data/`. Beim naechsten Neustart (meist nach einem Update) schien sie
geloescht. Diese Tests halten die Uebernahme fest.
"""
import sys, os, json, pathlib, shutil, subprocess, tempfile

WURZEL = pathlib.Path(__file__).resolve().parents[1]


def paths_mit(gm_data):
    """core.paths in einem eigenen Prozess laden — das Modul merkt sich DATA
    beim Import, ein Nachladen im selben Prozess waere nicht aussagekraeftig."""
    umg = dict(os.environ)
    umg.pop("GM_DATA", None)
    if gm_data:
        umg["GM_DATA"] = gm_data
    code = ("import json;from core.paths import DATA, ROOT, alte_orte;"
            "print(json.dumps([str(DATA), str(ROOT),"
            "[str(p) for p in alte_orte('wallbox.json')]]))")
    r = subprocess.run([sys.executable, "-c", code], cwd=WURZEL, env=umg,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_ohne_gm_data_derselbe_ordner_wie_der_dienst():
    data, root, _ = paths_mit(None)
    # frueher war das der Projektordner selbst — und damit ein anderer Ort
    # als der, den der systemd-Dienst benutzt
    assert data == str(pathlib.Path(root) / "data"), data
    print("ok ohne GM_DATA wird data/ benutzt")


def test_gm_data_hat_vorrang():
    d = tempfile.mkdtemp()
    data, _, _ = paths_mit(d)
    assert data == d
    print("ok GM_DATA hat Vorrang")


def test_uebernahme_aus_altem_ort():
    """Eine im Projektordner liegende Config muss beim Start einziehen."""
    spiel = pathlib.Path(tempfile.mkdtemp()) / "wallbox"
    shutil.copytree(WURZEL, spiel, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".venv", "data", "*.pyc"))
    alt = spiel / "wallbox.json"
    alt.write_text(json.dumps({"meter": {"mode": "mqtt", "host": "192.168.1.60"},
                               "chargepoints": []}), encoding="utf-8")
    umg = dict(os.environ); umg.pop("GM_DATA", None)
    code = ("import app, json;"
            "c = app._load_cfg();"
            "print(json.dumps([c['meter'], c.get('_quelle','')]))")
    r = subprocess.run([sys.executable, "-c", code], cwd=spiel, env=umg,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    meter, quelle = json.loads(r.stdout.strip().splitlines()[-1])
    assert meter["host"] == "192.168.1.60", meter
    # jetzt muss sie am neuen Ort liegen und der alte Platz geraeumt sein
    assert (spiel / "data" / "wallbox.json").exists()
    assert not alt.exists() and (spiel / "wallbox.json.alt").exists()
    assert quelle.endswith("data/wallbox.json"), quelle
    print("ok Konfiguration zieht aus dem alten Ort um")


def test_vorhandene_config_wird_nicht_ueberschrieben():
    spiel = pathlib.Path(tempfile.mkdtemp()) / "wallbox"
    shutil.copytree(WURZEL, spiel, ignore=shutil.ignore_patterns(
        ".git", "__pycache__", ".venv", "data", "*.pyc"))
    (spiel / "wallbox.json").write_text('{"meter":{"mode":"mock"},"chargepoints":[]}')
    (spiel / "data").mkdir()
    (spiel / "data" / "wallbox.json").write_text(
        '{"meter":{"mode":"mqtt","host":"richtig"},"chargepoints":[]}')
    umg = dict(os.environ); umg.pop("GM_DATA", None)
    r = subprocess.run([sys.executable, "-c",
                        "import app, json; print(json.dumps(app._load_cfg()['meter']))"],
                       cwd=spiel, env=umg, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    m = json.loads(r.stdout.strip().splitlines()[-1])
    # die aktuelle Datei gewinnt — eine alte Leiche darf sie nicht ersetzen
    assert m["host"] == "richtig", m
    print("ok vorhandene Konfiguration bleibt unangetastet")


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Datenort-Tests bestanden")
