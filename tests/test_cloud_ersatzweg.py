"""Cloud-Ersatzweg: haengt die Box im LAN, weiter ueber die SmartLife-Cloud (25.11.2026).

    python3 tests/test_cloud_ersatzweg.py

Anlass: Zum zweiten Mal (14.11., 25.11.) nahm die Box im LAN keine Anmeldung mehr an; die SmartLife-App zeigte
zur selben Zeit Werte. Bisher half nur Sicherung aus/an — GridMine zeichnete solange eine Luecke auf.
Nutzer: „Das ist ja keine Problemloesung, wenn ich immer die Sicherung aus- und anschalten muss."

Waechter:
  * ohne eingerichteten Ersatzweg verhaelt sich der Treiber genau wie vorher (kein Cloud-Aufruf),
  * erst nach ZWEI lokalen Fehlschlaegen in Folge kommen die Werte aus der Cloud — mit den Datenpunkt-Nummern,
    die der Treiber kennt; die Statuskarte sagt „cloud",
  * im Cloud-Betrieb wird lokal nur alle LOKAL_WIEDER_S probiert, die Cloud hoechstens alle STATUS_S gefragt,
  * antwortet die Box lokal wieder, geht es dorthin zurueck,
  * Schalten: im Cloud-Betrieb ueber die Cloud (Name statt Nummer); lokal zweimal gescheitert -> Cloud,
  * Box auch in der Cloud offline -> lesbarer Fehler, nichts erfunden,
  * Anmeldung liegt nur in der Datei (0600); erneuerte Tokens werden gesichert; keine Antwort der Steuerung
    enthaelt Benutzercode oder Token; Ausschalten loescht die Datei.
"""
import asyncio
import json
import os
import pathlib
import stat
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import core.tuya_cloud as TC  # noqa: E402
import drivers.tuya as WT  # noqa: E402
from core.tuya_cloud import CloudWeg, CloudFehler  # noqa: E402

DEV = "bf0123456789abcdefgh"
ZUGANG = {"user_code": "GEHEIMCODE", "terminal_id": "term-1", "endpoint": "https://apigw.tuyaeu.com",
          "token_info": {"t": 1, "uid": "u1", "expire_time": 7200, "access_token": "ACCESS-GEHEIM",
                         "refresh_token": "REFRESH-GEHEIM"}}
CLOUD_STATUS = [{"code": "switch", "value": True}, {"code": "power_total", "value": 7100},
                {"code": "work_state", "value": "charger_charging"}, {"code": "forward_energy_total", "value": 12345},
                {"code": "charge_cur_set", "value": 10}, {"code": "temp_current", "value": 31}]


class FakeSdk:
    """Wie tuya_sharing: Manager(client_id, user_code, terminal_id, end_point, token_response, listener)."""
    def __init__(self, online=True, status=None, kaputt=False):
        self.online, self.kaputt = online, kaputt
        self.status = list(CLOUD_STATUS if status is None else status)
        self.gets, self.posts, self.manager = [], [], None
        sdk = self

        class SharingTokenListener:
            def update_token(self, token_info):
                pass
        self.SharingTokenListener = SharingTokenListener

        class Api:
            def get(self, pfad, params=None):
                sdk.gets.append((pfad, params))
                if sdk.kaputt:
                    raise ConnectionError("weg")
                if pfad.endswith("/status"):
                    return {"success": True, "result": {"dpStatusRelationDTOS": [
                        {"dpId": 18, "statusCode": "switch"}, {"dpId": 9, "statusCode": "power_total"}]}}
                return {"success": True, "result": [{"id": DEV, "online": sdk.online, "status": sdk.status}]}

            def post(self, pfad, params=None, body=None):
                sdk.posts.append((pfad, body))
                return {"success": True, "result": True}

        class Manager:
            def __init__(self, client_id, user_code, terminal_id, end_point, token_response, listener):
                self.args = (client_id, user_code, terminal_id, end_point, token_response)
                self.listener = listener
                self.customer_api = Api()
                sdk.manager = self

            def unload(self):
                pass
        self.Manager = Manager


class Uhr:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _weg(sdk=None, uhr=None):
    d = pathlib.Path(tempfile.mkdtemp())
    w = CloudWeg(d / "tuya_cloud.json", sdk=sdk or FakeSdk(), uhr=uhr or Uhr())
    return w


class Box(WT.TuyaWallbox):
    """Treiber mit gespieltem lokalem Zugang: lokal_ok steuert, ob die Box im LAN antwortet."""
    def __init__(self, **kw):
        super().__init__(ip="192.168.1.8", device_id=DEV, local_key="k" * 16, **kw)
        self.lokal_ok = True
        self.lokal_versuche = 0
        self.gesetzt = []

    def _lokal_status_sync(self):
        self.lokal_versuche += 1
        if not self.lokal_ok:
            raise RuntimeError("Check device key or version")
        return {"18": False, "9": 0, "3": "charger_free"}

    def _connect(self):
        box = self

        class D:
            def set_value(self, dp, value):
                if not box.lokal_ok:
                    return {"Error": "Network Error: Device Unreachable"}
                box.gesetzt.append((dp, value))
                return {}
        return D()

    def _drop(self):
        pass


def test_ohne_ersatzweg_wie_bisher():
    sdk = FakeSdk()
    b = Box()
    b.cloud = CloudWeg(pathlib.Path(tempfile.mkdtemp()) / "fehlt.json", sdk=sdk)
    assert not b.cloud.aktiv
    b.lokal_ok = False
    for _ in range(3):
        try:
            b._status_sync()
            raise AssertionError("ohne Ersatzweg darf nichts aus der Cloud kommen")
        except RuntimeError as e:
            assert "Check device key" in str(e)
    assert sdk.gets == [] and sdk.manager is None and b.weg == "lokal"
    print("ok ohne Ersatzweg wie bisher")


def test_zwei_fehlschlaege_dann_cloud_mit_dp_nummern():
    sdk, uhr = FakeSdk(), Uhr()
    w = _weg(sdk, uhr)
    w.speichern(ZUGANG)
    b = Box()
    b.cloud = w
    assert b._status_sync()["18"] is False and b.weg == "lokal"
    b.lokal_ok = False
    try:
        b._status_sync()
        raise AssertionError("ein einzelner Aussetzer bleibt ein Aussetzer")
    except RuntimeError:
        pass
    assert sdk.gets == []
    dps = b._status_sync()
    assert b.weg == "cloud"
    assert dps["18"] is True and dps["9"] == 7100 and dps["3"] == "charger_charging" and dps["1"] == 12345, dps
    assert dps["4"] == 10 and dps["24"] == 31
    assert all(k.isdigit() for k in dps), "Treiber kennt nur Nummern"
    assert sdk.manager.args[0] == TC.CLIENT_ID and sdk.manager.args[1] == "GEHEIMCODE"
    print("ok zwei Fehlschläge, dann Cloud mit Datenpunkt-Nummern")


def test_cloud_takt_und_zurueck_nach_lokal():
    sdk, uhr = FakeSdk(), Uhr()
    w = _weg(sdk, uhr)
    w.speichern(ZUGANG)
    b = Box()
    b.cloud = w
    b.lokal_ok = False
    jetzt = [5000.0]
    alt_time = WT.time.time
    WT.time.time = lambda: jetzt[0]
    try:
        for _ in range(2):
            try:
                b._status_sync()
            except RuntimeError:
                pass
        assert b.weg == "cloud"
        versuche, abfragen = b.lokal_versuche, len([g for g in sdk.gets if "detail" in g[0]])
        b._status_sync()
        b._status_sync()
        assert b.lokal_versuche == versuche, "im Cloud-Betrieb nicht bei jedem Takt lokal haengen"
        assert len([g for g in sdk.gets if "detail" in g[0]]) == abfragen, "Cloud höchstens alle STATUS_S"
        uhr.t += TC.STATUS_S + 1
        b._status_sync()
        assert len([g for g in sdk.gets if "detail" in g[0]]) == abfragen + 1
        # nach LOKAL_WIEDER_S wieder lokal probieren — Box antwortet: zurück
        jetzt[0] += TC.LOKAL_WIEDER_S + 1
        b.lokal_ok = True
        dps = b._status_sync()
        assert b.weg == "lokal" and dps["3"] == "charger_free" and b.lokal_versuche == versuche + 1
    finally:
        WT.time.time = alt_time
    print("ok Cloud-Takt begrenzt, zurück nach lokal")


def test_schalten_ueber_die_cloud():
    sdk = FakeSdk()
    w = _weg(sdk)
    w.speichern(ZUGANG)
    b = Box()
    b.cloud = w
    b.schreib_pause_s = 0
    # lokal zweimal gescheitert -> der Befehl geht über die Cloud, nicht verloren
    b.lokal_ok = False
    assert b._set_sync(18, True) is True
    assert sdk.posts[-1] == (f"/v1.1/m/thing/{DEV}/commands", {"commands": [{"code": "switch", "value": True}]})
    # im Cloud-Betrieb gleich über die Cloud
    b.weg = "cloud"
    b._set_sync(4, 13)
    assert sdk.posts[-1][1] == {"commands": [{"code": "charge_cur_set", "value": 13}]}
    try:
        w.setzen(DEV, 99, 1)
        raise AssertionError("unbekannter Datenpunkt")
    except CloudFehler:
        pass
    # ohne Ersatzweg bleibt es beim Fehler
    b2 = Box()
    b2.schreib_pause_s = 0
    b2.lokal_ok = False
    try:
        b2._set_sync(18, True)
        raise AssertionError("ohne Ersatzweg kein Erfolg melden")
    except RuntimeError:
        pass
    print("ok Schalten über die Cloud")


def test_box_auch_in_der_cloud_offline():
    for sdk, teil in ((FakeSdk(online=False), "auch in der Cloud offline"), (FakeSdk(kaputt=True), "Cloud nicht"),
                      (FakeSdk(status=[]), "keine Werte")):
        w = _weg(sdk)
        w.speichern(ZUGANG)
        b = Box()
        b.cloud = w
        b.lokal_ok = False
        for _ in range(2):
            try:
                b._status_sync()
                raise AssertionError("nichts erfinden")
            except RuntimeError as e:
                letzter = str(e)
        assert teil in letzter, letzter
        assert "GEHEIM" not in letzter
    st = asyncio.run(b.get_stats())
    assert st.online is False and "GEHEIM" not in json.dumps(st.raw)
    print("ok Box auch in der Cloud offline: lesbarer Fehler")


def test_anmeldung_nur_in_der_datei_und_token_erneuert():
    sdk = FakeSdk()
    w = _weg(sdk)
    for schlecht in (None, {}, {"user_code": "x"}):
        try:
            w.speichern(schlecht)
            raise AssertionError(schlecht)
        except CloudFehler:
            pass
    assert not w.aktiv
    w.speichern(ZUGANG)
    assert stat.S_IMODE(os.stat(w.datei).st_mode) == 0o600
    w.status(DEV)
    sdk.manager.listener.update_token({"t": 2, "uid": "u1", "expire_time": 7200, "access_token": "NEU",
                                       "refresh_token": "NEU-R", "fremd": "x"})
    d = json.loads(w.datei.read_text())
    assert d["token_info"]["access_token"] == "NEU" and "fremd" not in d["token_info"]
    assert d["user_code"] == "GEHEIMCODE"
    assert stat.S_IMODE(os.stat(w.datei).st_mode) == 0o600
    w.entfernen()
    assert not w.aktiv and not w.datei.exists()
    print("ok Anmeldung nur in der Datei, Token erneuert, Ausschalten löscht")


def test_steuerung_gibt_nie_zugangsdaten_heraus():
    app = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    i = app.index("async def api_schluessel_pruefen(")
    teil = app[i:app.index("\n@app.", i)]
    assert 'ctl.cloud.speichern(e.pop("zugang", None))' in teil and 'e.pop("zugang", None)' in teil
    assert '"zugang"' not in teil.split("return {\"stand\": \"fertig\"")[1]
    i = app.index("async def api_cloud_get(")
    assert 'return {"aktiv": ctl.cloud.aktiv, "fehler": ctl.cloud.letzter_fehler or None}' in app[i:i + 400]
    assert "cp.driver.cloud = self.cloud" in app and "ersatz.driver.cloud = getattr(ctl, \"cloud\", None)" in app
    seite = (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert 'id="kCloud"' in seite and "JSON.stringify({cloud: $('kCloud').checked})" in seite
    assert "c.weg === 'cloud'" in seite and "method:'DELETE'" in seite
    print("ok Steuerung gibt nie Zugangsdaten heraus")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("alle Cloud-Ersatzweg-Tests bestanden")
