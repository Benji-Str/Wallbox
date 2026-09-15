"""Dauerhafte Aufzeichnung: Energie, Tageswechsel, Luecken, Aufraeumen."""
import sys, pathlib, tempfile, json, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from core.historie import Historie

# 1. Januar 2025, 00:00 Ortszeit — als Zahl, damit die Tests unabhaengig
# von der Zeitzone des Rechners bleiben.
START = time.mktime((2025, 1, 1, 0, 0, 0, 0, 0, -1))
UHR = [START]
def uhr(): return UHR[0]


def neu(**kw):
    UHR[0] = START
    d = tempfile.mkdtemp()
    return Historie(d, clock=uhr, aufloesung_s=60, **kw), pathlib.Path(d)


def lauf(h, sekunden, schritt=10, **werte):
    for _ in range(int(sekunden / schritt)):
        UHR[0] += schritt
        h.merke(**werte)


def test_energie_stimmt():
    h, _ = neu()
    # 3600 W eine Stunde lang = 3,6 kWh — nachrechenbar, nicht geraten
    lauf(h, 3600, 10, pv=3600, netz=-3600, haus=0, laden=0)
    e = h.heute()
    assert abs(e["pv"] - 3.6) < 0.02, e
    assert abs(e["einspeisung"] - 3.6) < 0.02, e
    assert e["bezug"] == 0.0
    print("ok Energie stimmt")


def test_netz_wird_getrennt():
    h, _ = neu()
    lauf(h, 1800, 10, netz=2000)      # 0,5 h Bezug
    lauf(h, 1800, 10, netz=-2000)     # 0,5 h Einspeisung
    e = h.heute()
    assert abs(e["bezug"] - 1.0) < 0.02 and abs(e["einspeisung"] - 1.0) < 0.02
    print("ok Bezug und Einspeisung getrennt")


def test_luecke_erfindet_keine_energie():
    h, _ = neu()
    lauf(h, 600, 10, pv=1000)
    UHR[0] += 4 * 3600          # Dienst war vier Stunden aus
    h.merke(pv=1000)
    e = h.heute()
    # waeren die 4 h mitgezaehlt worden, staenden hier ueber 4 kWh
    assert e["pv"] < 0.2, e
    assert e["luecken_s"] >= 4 * 3600
    print("ok Ausfall wird nicht zu Energie")


def test_fehlender_wert_ist_keine_null():
    h, _ = neu()
    lauf(h, 600, 10, pv=1000, haus=None)
    e = h.heute()
    assert e["haus"] == 0.0 and e["luecken_s"] >= 590
    # 0,0 kWh Haus ist hier nicht "es wurde nichts verbraucht", sondern
    # "nicht gemessen" — deshalb muss die Luecke danebenstehen
    print("ok fehlender Wert wird als Luecke gefuehrt")


def test_tageswechsel():
    h, _ = neu()
    lauf(h, 1800, 10, pv=1000)
    UHR[0] = time.mktime((2025, 1, 2, 0, 0, 30, 0, 0, -1))
    h.merke(pv=1000)
    lauf(h, 600, 10, pv=2000)
    tage = h.tage(10)
    assert [t["tag"] for t in tage] == ["2025-01-01", "2025-01-02"]
    assert tage[0]["pv"] > 0 and tage[1]["pv"] > 0
    print("ok Tageswechsel trennt sauber")


def test_dateien_und_neustart():
    h, d = neu()
    lauf(h, 3600, 10, pv=1000, netz=-500, haus=500, laden=0, soc=80)
    assert (d / "verlauf" / "2025-01-01.csv").exists()
    zeilen = (d / "verlauf" / "2025-01-01.csv").read_text().strip().split("\n")
    assert zeilen[0].startswith("t,pv,netz")
    assert len(zeilen) >= 59, len(zeilen)     # eine Zeile je Minute
    vorher = h.heute()["pv"]
    # Neustart: dieselbe Uhr, derselbe Ordner. Auf die Platte geschrieben
    # wird im Takt der Aufloesung — bei einem harten Stromausfall fehlt
    # daher bis zu eine Minute. Das ist gewollt: jede Sekunde zu schreiben
    # waere fuer eine Anzeige zuviel Verschleiss.
    h2 = Historie(d, clock=uhr, aufloesung_s=60)
    assert abs(h2.heute()["pv"] - vorher) <= 1000 * 60 / 3_600_000 + 1e-6
    lauf(h2, 600, 10, pv=1000)
    assert h2.heute()["pv"] > vorher       # zaehlt weiter, faengt nicht neu an
    print("ok ueberlebt den Neustart")


def test_tagesabfrage():
    h, _ = neu()
    lauf(h, 3600, 10, pv=1000, haus=None)
    t = h.tag("2025-01-01", punkte=12)
    assert t["n"] == 12 and len(t["pv"]) == 12
    assert all(x == 1000 for x in t["pv"])
    assert all(x is None for x in t["haus"])   # Luecken bleiben Luecken
    assert t["energie"]["pv"] > 0
    print("ok Tagesabfrage")


def test_unbekannter_tag():
    h, _ = neu()
    t = h.tag("1999-12-31")
    assert t["n"] == 0 and t["pv"] == []
    print("ok unbekannter Tag")


def test_aufraeumen():
    h, d = neu(tage_behalten=2)
    for tagnr in (1, 2, 3, 4):
        UHR[0] = time.mktime((2025, 1, tagnr, 12, 0, 0, 0, 0, -1))
        h.merke(pv=100)
        UHR[0] += 70
        h.merke(pv=100)
    csv = sorted(p.name for p in (d / "verlauf").glob("*.csv"))
    assert csv == ["2025-01-03.csv", "2025-01-04.csv"], csv
    # die Tagesenergie ueberlebt das Aufraeumen — sie ist winzig
    assert len(h.tage(99)) == 4
    print("ok alte Verlaufsdateien werden geloescht")


def test_kaputte_datei_bremst_nicht():
    h, d = neu()
    (d / "tagesenergie.json").write_text("{kaputt")
    h2 = Historie(d, clock=uhr)
    h2.merke(pv=100)
    assert h2.heute()["pv"] == 0.0
    print("ok kaputte Statistik wirft den Dienst nicht")


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Historie-Tests bestanden")
