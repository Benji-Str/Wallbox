"""Verbindung testen (17.11.2026): IP, Protokoll und Schluessel nachweislich richtig, trotzdem
„Check device key or version" — wo genau haengt die Anmeldung?

Waechter: eingetragene Version zuerst, dann die anderen; je Versuch frische Verbindung; das Protokoll von
tinytuya wird nur fuer die eingetragene Version mitgeschrieben; der Schluessel steht nirgends (auch nicht
hex); waehrend des Tests fragt die Regelung die Box nicht.
"""
import json, logging, sys, types, pathlib, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

KEY = "abcdefgh12345678"
fake = types.ModuleType("tinytuya")
fake.__version__ = "1.17.4"
fake.geraete = []


class Device:
    def __init__(self, did, ip, key, version=3.4):
        self.version, self.zu = version, False
        fake.geraete.append(self)
    def set_socketTimeout(self, s): pass
    def set_socketRetryLimit(self, n): pass
    def status(self):
        logging.getLogger("tinytuya").debug(f"negotiate session key with {KEY} / {KEY.encode().hex()}")
        if self.version == 3.5:
            return {"dps": {"1": 1, "4": 8}}
        return {"Error": "Check device key or version", "Err": "914", "Payload": None}
    def close(self): self.zu = True


fake.Device = Device
sys.modules["tinytuya"] = fake
import socket
socket.create_connection = lambda adr, timeout=3: type("S", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: False})()
time.sleep = lambda s: None

from drivers.tuya import TuyaWallbox


def box():
    return TuyaWallbox(ip="192.168.1.8", name="Wallbox", device_id="bf09", local_key=KEY, protocol="3.4")


def test_reihenfolge_ergebnis_und_kein_schluessel():
    d = box().verbindungstest()
    assert [v["version"] for v in d["versuche"]] == ["3.4", "3.5", "3.3"], "eingetragene Version zuerst"
    assert d["versuche"][0]["ergebnis"].startswith("Check device key or version (Err 914)")
    assert d["versuche"][1]["ergebnis"] == "OK — 2 Datenpunkte"
    assert d["tinytuya"] == "1.17.4" and d["tcp"].startswith("offen")
    assert len(d["protokoll"]) == 1, "nur für die eingetragene Version mitgeschrieben"
    roh = json.dumps(d)
    assert KEY not in roh and KEY.encode().hex() not in roh and "•••" in d["protokoll"][0]
    assert all(g.zu for g in fake.geraete), "jede Testverbindung wird wieder geschlossen"
    assert not logging.getLogger("tinytuya").handlers, "der Mitschreiber wird wieder entfernt"
    print("ok Reihenfolge, Ergebnis und kein Schlüssel")


def test_regelung_pausiert_waehrend_des_tests():
    b = box()
    b._test_bis = time.time() + 30
    try:
        b._status_sync(); raise AssertionError("darf nicht verbinden")
    except RuntimeError as e:
        assert "Verbindungstest" in str(e)
    b.verbindungstest()
    assert b._test_bis == 0.0, "danach fragt die Regelung wieder"
    print("ok Regelung pausiert während des Tests")


def test_oberflaeche():
    html = (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="tTest"' in html and "/testen`" in html and "o.textContent = [" in html, "nur als Text"
    print("ok Oberfläche")


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Verbindungstest-Tests bestanden")
