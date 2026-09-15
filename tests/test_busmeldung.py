"""Was die Wallbox dem Hauptsystem meldet.

GridMine ist das Hauptsystem, diese Steuerung eine Erweiterung davon. Der
Vertrag steht drueben in `core/kacheln.py`; hier wird festgehalten, dass wir
uns daran halten — sonst faellt es erst am Wanddisplay auf, wo niemand mehr
nachvollziehen kann, welche Seite sich geaendert hat.
"""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import app as wallbox_app

FARBEN = ("blau", "gruen", "rot", "gelb", "grau", "lila")


class FakeMqtt:
    """Nimmt entgegen, was sonst zum Broker ginge."""
    def __init__(self): self.gesendet = []
    def publish(self, werte): pass
    def publish_json(self, thema, nutzlast, retain=True):
        self.gesendet.append((thema, nutzlast)); return True


def steuerung():
    cfg = {"interval_s": 10, "meter": {"mode": "mock"},
           "chargepoints": [{"id": "wb1", "type": "mock", "ip": "1.2.3.4",
                             "name": "Wallbox"}]}
    return wallbox_app.WallboxController(cfg)


def meldung(**cp_werte):
    c = {"id": "wb1", "name": "Wallbox", "online": True, "plugged": True,
         "charging": True, "power_w": 6900, "session_kwh": 4.2, "amp": 16,
         "mode": "pv", "reason": "PV: 6900 W Ueberschuss"}
    c.update(cp_werte)
    ctl, m = steuerung(), FakeMqtt()
    ctl._melde_bus(m, c)
    assert m.gesendet, "es wurde nichts gemeldet"
    return m.gesendet[-1]


def test_thema_und_pflichtfelder():
    thema, n = meldung()
    assert thema == "gridmine/wallbox/wb1/status", thema
    for feld in ("art", "id", "name", "zeit", "zustand", "text",
                 "leistung_w", "gueltig_s", "kacheln"):
        assert feld in n, feld
    assert n["art"] == "wallbox" and n["id"] == "wb1"
    assert isinstance(n["kacheln"], list) and n["kacheln"]
    print("ok Thema und Pflichtfelder")


def test_kacheln_halten_den_vertrag():
    _, n = meldung()
    for k in n["kacheln"]:
        assert isinstance(k["titel"], str) and k["titel"]
        assert k["farbe"] in FARBEN, k["farbe"]
        assert isinstance(k["rang"], int)
        assert k["wert"] is None or isinstance(k["wert"], (int, float))
    print("ok Kacheln halten den Vertrag")


def test_offline_meldet_unbekannt_nicht_null():
    """Eine nicht erreichbare Box hat nicht 0 W — sie hat keinen Messwert.
    0 W wuerde am Display aussehen, als stuende sie still und alles sei gut."""
    _, n = meldung(online=False, charging=False, power_w=0)
    assert n["leistung_w"] is None
    assert n["kacheln"][0]["wert"] is None
    assert n["zustand"] == "offline"
    print("ok offline heisst unbekannt, nicht null")


def test_zustand_in_klartext():
    assert meldung(charging=True)[1]["zustand"] == "laedt"
    assert meldung(charging=False, plugged=True)[1]["zustand"] == "angesteckt"
    assert meldung(charging=False, plugged=False)[1]["zustand"] == "bereit"
    print("ok Zustand in Klartext")


def test_gueltigkeit_passt_zum_takt():
    _, n = meldung()
    # Der Empfaenger muss wissen, wie lange ein Wert frisch ist. Zu kurz und
    # das Display meldet dauernd Ausfall, zu lang und es zeigt Altes an.
    assert n["gueltig_s"] >= 30
    print("ok Gueltigkeitsdauer haengt am Regeltakt")


def test_meldung_ist_json_faehig():
    _, n = meldung()
    json.loads(json.dumps(n))       # nichts Exotisches drin
    print("ok Meldung laesst sich als JSON senden")


def test_ohne_prefix_keine_meldung():
    ctl, m = steuerung(), FakeMqtt()
    ctl.cfg["bus_prefix"] = ""
    ctl._melde_bus(m, {"id": "wb1", "name": "W", "online": True, "plugged": True,
                       "charging": True, "power_w": 1, "session_kwh": 0,
                       "amp": 8, "mode": "pv", "reason": ""})
    assert not m.gesendet, "abgeschaltet heisst abgeschaltet"
    print("ok ohne Prefix wird nichts gemeldet")


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Bus-Meldungstests bestanden")
