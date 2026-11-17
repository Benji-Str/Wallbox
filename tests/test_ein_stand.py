"""Ein Stand (17.11.2026): Die Steuerung lief auf einem Arbeitszweig, eine Korrektur auf main kam nie an.
„Software aktualisieren" stellt die Installation auf main um — aber nur, wenn main schon alles von ihrem
Zweig enthaelt und nichts von Hand geaendert ist. Geprueft mit echten Git-Ablagen in einem Temp-Ordner.
"""
import os, subprocess, sys, tempfile, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import app as A

UMG = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
       "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_GLOBAL": "/dev/null"}


def g(ort, *a):
    r = subprocess.run(["git", "-C", str(ort), *a], capture_output=True, text=True, env=UMG)
    assert r.returncode == 0 or a[0] in ("merge-base",), (a, r.stderr)
    return r.stdout.strip()


def aufbau():
    t = pathlib.Path(tempfile.mkdtemp())
    quelle, ablage, geraet = t / "quelle", t / "ablage.git", t / "geraet"
    subprocess.run(["git", "init", "-q", "-b", "main", str(quelle)], env=UMG, check=True)
    (quelle / "a.txt").write_text("1"); g(quelle, "add", "-A"); g(quelle, "commit", "-qm", "eins")
    g(quelle, "checkout", "-qb", "arbeit"); (quelle / "a.txt").write_text("2"); g(quelle, "commit", "-qam", "zwei")
    subprocess.run(["git", "clone", "-q", "--bare", str(quelle), str(ablage)], env=UMG, check=True)
    subprocess.run(["git", "clone", "-q", "-b", "arbeit", str(ablage), str(geraet)], env=UMG, check=True)
    return quelle, ablage, geraet


def test_bleibt_wenn_main_etwas_fehlt():
    quelle, ablage, geraet = aufbau()
    satz = A._auf_main_umstellen(str(geraet))
    assert satz.startswith("Bleibt auf Zweig arbeit") and g(geraet, "rev-parse", "--abbrev-ref", "HEAD") == "arbeit"
    print("ok bleibt, wenn main etwas fehlt")


def test_stellt_um_wenn_main_alles_hat():
    quelle, ablage, geraet = aufbau()
    g(quelle, "checkout", "-q", "main"); g(quelle, "merge", "-q", "--no-ff", "arbeit", "-m", "zusammen")
    g(quelle, "push", "-q", str(ablage), "main")
    satz = A._auf_main_umstellen(str(geraet))
    assert satz == "Von Zweig arbeit auf main umgestellt.", satz
    assert g(geraet, "rev-parse", "--abbrev-ref", "HEAD") == "main"
    assert g(geraet, "rev-parse", "--abbrev-ref", "main@{upstream}") == "origin/main", "holt künftig main"
    assert A._auf_main_umstellen(str(geraet)) == "", "steht schon auf main"
    print("ok stellt um, wenn main alles hat")


def test_bleibt_bei_handaenderung():
    quelle, ablage, geraet = aufbau()
    g(quelle, "checkout", "-q", "main"); g(quelle, "merge", "-q", "--no-ff", "arbeit", "-m", "zusammen")
    g(quelle, "push", "-q", str(ablage), "main")
    (geraet / "a.txt").write_text("von Hand")
    assert "von Hand geändert" in A._auf_main_umstellen(str(geraet))
    assert g(geraet, "rev-parse", "--abbrev-ref", "HEAD") == "arbeit" and (geraet / "a.txt").read_text() == "von Hand"
    print("ok bleibt bei Handänderung")


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Ein-Stand-Tests bestanden")
