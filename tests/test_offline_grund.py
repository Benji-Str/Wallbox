"""Warum die Wallbox „offline" ist — sichtbar statt verschluckt (17.11.2026).

Anlass: Statusseite und Bus zeigten nur „offline". In der Fritzbox war die Box im WLAN, Port 6668 war
offen (Test-NetConnection: True) — ob Adresse, Schluessel oder Protokoll nicht stimmen, konnte niemand
sehen, weil der Treiber den Fehler nur in `raw["error"]` ablegte und ihn nirgends zeigte.

Waechter: jede Fehlerart von tinytuya wird ein eigener, lesbarer Satz; der local_key steht nie darin;
der Ladepunkt gibt den Grund in `live()` weiter (nur offline); der Bus sendet ihn als Text; die
Oberflaeche zeigt ihn entschaerft an.
"""
import asyncio, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import drivers.tuya as WT
from drivers.tuya import fehler_grund

KEY = "a1b2c3d4e5f6g7h8"
IP = "192.168.1.8"


def test_jede_fehlerart_ein_eigener_satz():
    # Texte, wie tinytuya sie in {"Error": …} liefert (Fehlernummern 900–914)
    faelle = {
        "Check device key or version": "Schlüssel oder Protokoll",
        "Network Error: Device Unreachable": "nicht erreichbar",
        "Network Error: Unable to Connect": "nicht erreichbar",
        "Timeout Waiting for Device": "nicht rechtzeitig",
        "Unexpected Payload from Device": "Protokoll",
        "Invalid JSON Response from Device": "Protokoll",
        "[Errno 111] Connection refused": "nicht erreichbar",
    }
    saetze = set()
    for roh, erwartet in faelle.items():
        satz = fehler_grund(RuntimeError(roh), IP, KEY)
        assert erwartet in satz, (roh, satz)
        assert satz != "offline" and len(satz) <= 240
        saetze.add(satz)
    assert len(saetze) >= 4, "Adresse, Schlüssel, Zeitüberschreitung und Protokoll unterscheidbar"
    assert IP + ":6668" in fehler_grund(RuntimeError("Network Error: Device Unreachable"), IP, KEY)
    assert "tinytuya" in fehler_grund(ModuleNotFoundError("No module named 'tinytuya'"), IP, KEY)
    unbekannt = fehler_grund(RuntimeError("etwas ganz anderes"), IP, KEY)
    assert unbekannt.startswith("Die Box antwortet nicht: etwas ganz anderes")
    print("ok jede Fehlerart ein eigener Satz")


def test_der_schluessel_steht_nie_im_text():
    for roh in (f"bad key {KEY}", f"Check device key or version ({KEY})", KEY):
        assert KEY not in fehler_grund(RuntimeError(roh), IP, KEY), roh
    assert len(fehler_grund(RuntimeError("x" * 5000), IP, KEY)) <= 240
    print("ok der Schlüssel steht nie im Text")


class KaputteBox(WT.TuyaWallbox):
    def __init__(self, fehler, **kw):
        super().__init__(**kw)
        self.fehler = fehler
    def _status_sync(self):
        raise RuntimeError(self.fehler)


def test_ladepunkt_gibt_den_grund_weiter():
    from core.chargepoint import ChargePoint
    cfg = {"id": "wb1", "name": "Wallbox", "type": "tuya", "ip": IP, "device_id": "x", "local_key": KEY,
           "protocol": "3.4", "phases": 3, "min_a": 8, "max_a": 32, "min_switch_interval_s": 0,
           "charge": {"mode": "sofort"}}
    cp = ChargePoint(dict(cfg))
    cp.driver = KaputteBox("Check device key or version", **{k: v for k, v in cfg.items()
                                                                if k not in ("id", "type", "charge")})
    st = asyncio.run(cp.driver.get_stats())
    assert st.online is False and st.state == "offline"
    assert "Schlüssel oder Protokoll" in st.raw["error"] and KEY not in str(st.raw)
    quelle = (pathlib.Path(__file__).resolve().parents[1] / "core" / "chargepoint.py").read_text(encoding="utf-8")
    assert '"fehler": ((st.raw or {}).get("error") if st and not st.online else None)' in quelle, \
        "live() gibt den Grund weiter — und nur, solange die Box offline ist"
    print("ok Ladepunkt gibt den Grund weiter")


def test_bus_sendet_den_grund_als_text():
    meldung = _meldung
    _, n = meldung(online=False, charging=False, power_w=0, reason="Sofort: 16 A",
                   fehler="Die Box ist unter 192.168.1.8:6668 nicht erreichbar.")
    assert n["zustand"] == "offline" and n["text"].startswith("Die Box ist unter"), n["text"]
    _, n = meldung(online=True, reason="Sofort: 16 A", fehler=None)
    assert n["text"] == "Sofort: 16 A", "online bleibt der Regelgrund"
    _, n = meldung(online=False, charging=False, power_w=0, reason="Sofort: 16 A")
    assert n["text"] == "Sofort: 16 A", "ohne Grund (alte Meldung): wie bisher"
    print("ok Bus sendet den Grund als Text")


def test_oberflaeche_zeigt_ihn_entschaerft():
    html = (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert "${!c.online && c.fehler ? `<div class=\"err\"" in html and "${escH(c.fehler)}" in html
    assert "const escH = s =>" in html and "'<':'&lt;'" in html
    print("ok Oberfläche zeigt ihn entschärft")


def _meldung(**cp_werte):
    import app as wallbox_app

    class FakeMqtt:
        def __init__(self): self.gesendet = []
        def publish(self, werte): pass
        def publish_json(self, thema, nutzlast, retain=True):
            self.gesendet.append((thema, nutzlast)); return True

    cfg = {"interval_s": 10, "meter": {"mode": "mock"},
           "chargepoints": [{"id": "wb1", "type": "mock", "ip": "1.2.3.4", "name": "Wallbox"}]}
    c = {"id": "wb1", "name": "Wallbox", "online": True, "plugged": True, "charging": True,
         "power_w": 6900, "session_kwh": 4.2, "amp": 16, "mode": "sofort", "reason": "PV"}
    c.update(cp_werte)
    ctl, m = wallbox_app.WallboxController(cfg), FakeMqtt()
    ctl._melde_bus(m, c)
    return m.gesendet[-1]


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Offline-Grund-Tests bestanden")
