"""Verlauf: Ringpuffer, Eindampfen, und dass Unbekanntes unbekannt bleibt."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from core.verlauf import Verlauf

UHR = [0.0]
def uhr(): return UHR[0]


def neu(**kw):
    UHR[0] = 0.0
    return Verlauf(clock=uhr, **kw)


def test_abstand_wird_eingehalten():
    v = neu(punkte=10, abstand_s=10)
    assert v.merke(pv=100) is True
    UHR[0] += 3
    assert v.merke(pv=200) is False      # zu dicht — sonst reicht der
    UHR[0] += 8                          # Verlauf nur wenige Minuten zurueck
    assert v.merke(pv=300) is True
    assert len(v) == 2
    print("ok Mindestabstand")


def test_ringpuffer_laeuft_ueber():
    v = neu(punkte=5, abstand_s=0)
    for i in range(20):
        UHR[0] += 10
        v.merke(pv=i)
    assert len(v) == 5
    assert v.reihen(5)["pv"] == [15, 16, 17, 18, 19]
    print("ok Ringpuffer")


def test_unbekannt_bleibt_unbekannt():
    v = neu(punkte=10, abstand_s=0)
    for x in (None, None, None):
        UHR[0] += 10
        v.merke(pv=1000, haus=x)
    r = v.reihen(3)
    # 0 W Hausverbrauch waere eine Behauptung, die niemand gemessen hat
    assert r["haus"] == [None, None, None]
    assert r["pv"] == [1000, 1000, 1000]
    print("ok Unbekannt wird nicht zu null")


def test_mittelwert_ueber_bekannte():
    v = neu(punkte=10, abstand_s=0)
    for x in (100, None, 300, None):
        UHR[0] += 10
        v.merke(haus=x)
    assert v.reihen(2)["haus"] == [100, 300]
    assert v.reihen(1)["haus"] == [200]     # (100+300)/2, die Luecken zaehlen nicht
    print("ok Mittelwert nur ueber bekannte Werte")


def test_weniger_punkte_als_balken():
    v = neu(punkte=100, abstand_s=0)
    for i in range(3):
        UHR[0] += 10
        v.merke(pv=i * 10)
    r = v.reihen(40)
    # lieber drei Balken als drei Balken und 37 Luecken
    assert r["n"] == 3 and r["pv"] == [0, 10, 20]
    print("ok wenige Messpunkte")


def test_leer():
    v = neu()
    r = v.reihen(40)
    assert r["n"] == 0 and r["pv"] == [] and r["von"] is None
    print("ok leerer Verlauf")


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Verlauf-Tests bestanden")
