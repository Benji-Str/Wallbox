"""Schluessel neu holen mit Benutzercode + QR (17.11.2026, Waechter: „Check device key or version" seit 14.11.).

Waechter: der Ablauf wie bei Home Assistant (Kennung, Schema, QR-Inhalt); der Schluessel und der
Benutzercode stehen nie in einer Antwort; nach dem Holen wird die Tuya-Anmeldung beendet; eine fremde
oder fehlende Box gibt einen Satz statt eines Schluessels; ein QR-Code laeuft ab; die Steuerung traegt
den Schluessel ein, baut den Ladepunkt neu und sagt nur, OB er sich geaendert hat.
"""
import asyncio, json, sys, pathlib
from types import SimpleNamespace as N
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from core import schluessel as S

DEV = "bf09d30ac290349520gosa"
NEU = "N3u3rSchluessel!x"
CODE = "AbC123x"


class FakeSdk:
    """Nachbildung von tuya_sharing (LoginControl, Manager) — gleiche Aufrufe wie Home Assistant."""
    def __init__(self, geraete=None, bestaetigt_nach=1, qr_ok=True):
        self.geraete = geraete if geraete is not None else [N(id=DEV, name="Wallbox", local_key=NEU),
                                                             N(id="xyz", name="Steckdose", local_key="anders")]
        self.bestaetigt_nach, self.qr_ok = bestaetigt_nach, qr_ok
        self.aufrufe, self.abgemeldet = [], 0
        sdk = self

        class LoginControl:
            def __init__(self): self.n = 0
            def qr_code(self, client_id, schema, user_code):
                sdk.aufrufe.append(("qr", client_id, schema, user_code))
                return {"success": True, "result": {"qrcode": "TOK123"}} if sdk.qr_ok else {"success": False, "msg": "x"}
            def login_result(self, token, client_id, user_code):
                sdk.aufrufe.append(("login", token, client_id, user_code))
                self.n += 1
                if self.n < sdk.bestaetigt_nach:
                    return False, {"msg": "wartet"}
                return True, {"t": 1, "uid": "u", "expire_time": 7200, "access_token": "A", "refresh_token": "R",
                              "terminal_id": "term-1", "endpoint": "https://apigw.example"}

        class Manager:
            def __init__(self, client_id, user_code, terminal_id, endpoint, token_info):
                sdk.aufrufe.append(("manager", client_id, user_code, terminal_id, endpoint, tuple(sorted(token_info))))
                self.device_map = {}
            def update_device_cache(self):
                self.device_map = {g.id: g for g in sdk.geraete}
            def unload(self):
                sdk.abgemeldet += 1

        self.LoginControl, self.Manager = LoginControl, Manager


def test_ablauf_wie_home_assistant():
    sdk = FakeSdk(bestaetigt_nach=2)
    h = S.SchluesselHolen(sdk=sdk, uhr=lambda: 1000.0)
    assert h.start(CODE) == "tuyaSmart--qrLogin?token=TOK123"
    assert sdk.aufrufe[0] == ("qr", "HA_3y9q4ak7g4ephrvke", "haauthorize", CODE)
    assert h.pruefen(DEV) == {"stand": "wartet"}, "noch nicht in der App bestätigt"
    e = h.pruefen(DEV)
    assert e == {"stand": "fertig", "local_key": NEU, "name": "Wallbox"}, e
    m = [a for a in sdk.aufrufe if a[0] == "manager"][0]
    assert m[1:5] == ("HA_3y9q4ak7g4ephrvke", CODE, "term-1", "https://apigw.example")
    assert m[5] == ("access_token", "expire_time", "refresh_token", "t", "uid")
    assert sdk.abgemeldet == 1, "danach bei Tuya abmelden — kein dauerhafter Cloud-Zugang"
    assert h.pruefen(DEV) == {"stand": "abgelaufen"}, "ein QR-Code, eine Anmeldung"
    print("ok Ablauf wie Home Assistant")


def test_falsche_eingaben_und_fremde_box():
    h = S.SchluesselHolen(sdk=FakeSdk(), uhr=lambda: 0.0)
    for schlecht in ("", "ab", "mit leer", "<script>", "x" * 40, None):
        try:
            h.start(schlecht); raise AssertionError(schlecht)
        except S.Fehler as f:
            assert "Benutzercode" in str(f)
    try:
        S.SchluesselHolen(sdk=FakeSdk(qr_ok=False)).start(CODE); raise AssertionError("abgelehnt")
    except S.Fehler as f:
        assert "nicht angenommen" in str(f)
    sdk = FakeSdk(geraete=[N(id="xyz", name="Steckdose", local_key="anders")])
    h = S.SchluesselHolen(sdk=sdk, uhr=lambda: 0.0); h.start(CODE)
    try:
        h.pruefen(DEV); raise AssertionError("fremde Box")
    except S.Fehler as f:
        assert "nicht dabei" in str(f) and "Steckdose" in str(f) and "anders" not in str(f)
    assert sdk.abgemeldet == 1, "auch ohne Treffer abmelden"
    print("ok falsche Eingaben und fremde Box")


def test_qr_code_laeuft_ab():
    t = [0.0]
    h = S.SchluesselHolen(sdk=FakeSdk(bestaetigt_nach=99), uhr=lambda: t[0])
    h.start(CODE)
    t[0] = S.FRIST_S - 1
    assert h.pruefen(DEV) == {"stand": "wartet"}
    t[0] = S.FRIST_S + 1
    assert h.pruefen(DEV) == {"stand": "abgelaufen"}
    print("ok QR-Code läuft ab")


def test_steuerung_traegt_ein_und_verraet_nichts():
    import app as A
    sdk = FakeSdk()
    A._SCHLUESSEL = S.SchluesselHolen(sdk=sdk)
    A.ctl = A.WallboxController({"interval_s": 10, "meter": {"mode": "mock"},
                                 "chargepoints": [{"id": "wb1", "type": "mock", "ip": "1.2.3.4", "name": "Wallbox"}]})
    cp = A.ctl.chargepoints[0]
    cp.cfg.update(device_id=DEV, local_key="alterSchluessel")
    gespeichert = []
    A.ctl.save = lambda: gespeichert.append(1)
    r = asyncio.run(A.api_schluessel_start(cp.id, {"benutzercode": CODE}))
    assert r["qr"].startswith("tuyaSmart--qrLogin?token=") and CODE not in json.dumps(r)
    e = asyncio.run(A.api_schluessel_pruefen(cp.id))
    assert e == {"stand": "fertig", "geaendert": True, "name": "Wallbox"}, e
    neu = A.ctl.get(cp.id)
    assert neu.cfg["local_key"] == NEU and neu is not cp and gespeichert, "eingetragen, neu aufgebaut, gespeichert"
    assert NEU not in json.dumps(e) and "alterSchluessel" not in json.dumps(e)
    # zweites Mal mit demselben Schlüssel: „unverändert" — dann liegt es am Protokoll
    A._SCHLUESSEL = S.SchluesselHolen(sdk=FakeSdk())
    asyncio.run(A.api_schluessel_start(neu.id, {"benutzercode": CODE}))
    assert asyncio.run(A.api_schluessel_pruefen(neu.id))["geaendert"] is False
    # Fehler kommen als Satz, nicht als Ausnahme
    A._SCHLUESSEL = S.SchluesselHolen(sdk=FakeSdk(geraete=[]))
    asyncio.run(A.api_schluessel_start(neu.id, {"benutzercode": CODE}))
    f = asyncio.run(A.api_schluessel_pruefen(neu.id))
    assert f["stand"] == "fehler" and "nicht dabei" in f["text"]
    quelle = (pathlib.Path(A.__file__).parent / "app.py").read_text(encoding="utf-8")
    block = quelle[quelle.index("async def api_schluessel_start"):quelle.index('@app.get("/api/chargepoint/{cpid}/device")')]
    assert "print(" not in block and "local_key\"]}" not in block, "nie ins Protokoll"
    print("ok Steuerung trägt ein und verrät nichts")


def test_oberflaeche():
    html = (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
    assert '<script src="/qrcode.js"></script>' in html and 'id="kAuf"' in html and 'id="kCode"' in html
    assert "q.createSvgTag(" in html and "/schluessel/pruefen" in html and "Ich → Einstellungen → Konto und Sicherheit" in html
    assert "kMsg').textContent = t" in html.replace("const m = $('kMsg'); m.textContent = t", "kMsg').textContent = t"), \
        "Meldungen nur als Text"
    req = (pathlib.Path(__file__).resolve().parents[1] / "requirements.txt").read_text(encoding="utf-8")
    assert "tuya-device-sharing-sdk" in req, "„Software aktualisieren“ installiert die Bibliothek mit"
    print("ok Oberfläche")


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Schlüssel-Tests bestanden")
