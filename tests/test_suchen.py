"""Wallbox im Netz suchen (17.11.2026): Unter 192.168.1.8 lehnte ein Tuya-Geraet mit „Check device key or
version" ab, obwohl der Schluessel nachweislich stimmte. Sitzt dort ueberhaupt die Wallbox?

Waechter: die Suche erkennt die eigene Box an der device_id, nennt IP und Protokoll, vergleicht mit der
Einstellung; „Übernehmen" aendert nur IP und Protokoll und behaelt den Schluessel; keine Schluessel in
der Antwort.
"""
import asyncio, json, sys, types, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

DEV = "bf09d30ac290349520gosa"
FUNDE = {"192.168.1.8": {"ip": "192.168.1.8", "gwId": "bf1234steckdose", "version": "3.3", "productKey": "p1"},
         "192.168.1.23": {"ip": "192.168.1.23", "gwId": DEV, "version": "3.4", "productKey": "p2"}}

fake = types.ModuleType("tinytuya")
fake.aufrufe = []
def _scan(verbose, sekunden):
    fake.aufrufe.append((verbose, sekunden))
    return dict(FUNDE)
fake.deviceScan = _scan
sys.modules["tinytuya"] = fake

import app as A


def steuerung():
    A.ctl = A.WallboxController({"interval_s": 10, "meter": {"mode": "mock"},
                                 "chargepoints": [{"id": "wb1", "type": "mock", "ip": "192.168.1.8", "name": "Wallbox",
                                                   "device_id": DEV, "local_key": "geheimerSchluessel", "protocol": "3.4"}]})
    A.ctl.save = lambda: None
    return A.ctl.chargepoints[0]


def test_findet_die_eigene_box_an_der_kennung():
    cp = steuerung()
    d = asyncio.run(A.api_suchen(cp.id))
    assert fake.aufrufe[-1] == (False, 20), "wie tools/tuya_scan.py"
    assert d["wallbox"] == {"ip": "192.168.1.23", "id": DEV, "version": "3.4", "meine": True}, d
    assert d["geraete"][0]["meine"] and len(d["geraete"]) == 2, "die eigene zuerst"
    assert d["eingetragen"] == {"ip": "192.168.1.8", "protocol": "3.4"}
    assert "geheimerSchluessel" not in json.dumps(d)
    print("ok findet die eigene Box an der Kennung")


def test_uebernehmen_aendert_nur_ip_und_protokoll():
    cp = steuerung()
    asyncio.run(A.api_cp_device(cp.id, {"ip": "192.168.1.23", "protocol": "3.5"}))
    neu = A.ctl.get(cp.id)
    assert neu.cfg["ip"] == "192.168.1.23" and neu.cfg["protocol"] == "3.5"
    assert neu.cfg["local_key"] == "geheimerSchluessel" and neu.cfg["device_id"] == DEV, "Schlüssel bleibt"
    print("ok Übernehmen ändert nur IP und Protokoll")


def test_alte_verbindung_wird_vor_der_neuen_geschlossen():
    cp = steuerung()
    zu = []
    cp.driver._drop = lambda: zu.append(1)
    asyncio.run(A.api_cp_device(cp.id, {"protocol": "3.4"}))
    assert zu == [1], "sonst hält die alte Verbindung den einzigen Platz der Box besetzt"
    print("ok alte Verbindung wird vor der neuen geschlossen")


def test_ohne_eigene_box_kein_vorschlag():
    cp = steuerung()
    FUNDE.pop("192.168.1.23")
    d = asyncio.run(A.api_suchen(cp.id))
    assert d["wallbox"] is None and len(d["geraete"]) == 1 and not d["geraete"][0]["meine"]
    print("ok ohne eigene Box kein Vorschlag")


def test_oberflaeche():
    html = (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="sSuch"' in html and "/suchen`" in html and "escS(x.ip)" in html and "escS(x.id)" in html
    assert "body: JSON.stringify({ip: w.ip, protocol: w.version})" in html, "nur IP und Protokoll übernehmen"
    print("ok Oberfläche")


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Such-Tests bestanden")
