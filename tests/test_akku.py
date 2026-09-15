"""Akkugroesse lernen — und vor allem: nichts behaupten, was nicht belegt ist."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from core.akku import lerne, WIRKUNGSGRAD


def s(kwh, ende="voll", end=1000):
    return {"kwh": kwh, "ende": ende, "end": end}


def test_ohne_ladungen():
    e = lerne([])
    assert e["kwh"] is None and e["sicherheit"] == "keine"
    print("ok ohne Ladungen kein Wert")


def test_nur_abgebrochene_ladungen():
    e = lerne([s(6.0, "abgesteckt"), s(4.0, "beendet")])
    # 6 kWh sind trotzdem eine harte Untergrenze — aber eine schwache
    assert e["sicherheit"] == "schwach" and e["anzahl"] == 0
    assert abs(e["kwh"] - 6.0 * WIRKUNGSGRAD) < 0.05
    assert "nie von selbst" in e["text"]
    print("ok abgebrochene Ladungen ergeben nur eine schwache Untergrenze")


def test_groesste_zaehlt_nicht_der_mittelwert():
    e = lerne([s(3.0), s(11.0), s(2.0)])
    # Mittelwert waere 5,3 kWh — das waere schlicht falsch: wer bei 70 %
    # ansteckt, laedt eben wenig nach.
    assert abs(e["groesste_kwh"] - 11.0) < 0.01
    assert abs(e["kwh"] - 11.0 * WIRKUNGSGRAD) < 0.05
    print("ok die groesste Ladung zaehlt, nicht der Mittelwert")


def test_wirkungsgrad_macht_den_wert_kleiner():
    e = lerne([s(10.0)])
    # Aus der Steckdose kommt mehr, als im Akku ankommt. Ohne Abschlag waere
    # die "Untergrenze" zu hoch und damit keine.
    assert e["kwh"] < 10.0
    print("ok Ladeverluste werden abgezogen")


def test_sicherheit_waechst_mit_den_ladungen():
    assert lerne([s(10.0)])["sicherheit"] == "schwach"
    assert lerne([s(10.0), s(9.8)])["sicherheit"] == "mittel"
    assert lerne([s(10.0), s(9.8), s(9.9)])["sicherheit"] == "gut"
    # streuen die Werte, bleibt es unsicher — dann wurde unterschiedlich
    # voll angesteckt
    assert lerne([s(10.0), s(3.0), s(2.0)])["sicherheit"] == "mittel"
    print("ok Sicherheit haengt an Anzahl UND Streuung")


def test_rauschen_zaehlt_nicht():
    e = lerne([s(0.2), s(0.1)])
    assert e["kwh"] is None
    print("ok Kleinstmengen sind keine Ladung")


def test_text_verspricht_nichts():
    t = lerne([s(10.0), s(9.9), s(9.8)])["text"]
    assert t.startswith("Mindestens")
    assert "kaum groesser" in t          # Einschaetzung, keine Zusage
    print("ok der Text bleibt bei 'mindestens'")


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Akku-Tests bestanden")
