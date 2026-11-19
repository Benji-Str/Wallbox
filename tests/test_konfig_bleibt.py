"""Einstellungen bleiben nach dem Neuladen stehen (Nutzer 19.11.2026: „Muss die Werte immer neu eingeben
nach aktualisieren, Werte immer speichern").

Ursache: „Zähler speichern" schickte Netz L1–L3 und Verbrauch L1–L3 mit, `konfLaden()` trug sie beim
Öffnen aber nicht wieder ein. Die Felder standen leer da, und das nächste Speichern löschte die
gespeicherten Themen.

Wächter: Jedes Feld, das ein Speichern-Knopf der Konfiguration abschickt, wird beim Laden wieder
gesetzt (außer Passwort und Schlüssel, die nie zurückkommen). Und der Server gibt die Phasen-Themen
zurück, die er gespeichert hat.
"""
import asyncio, json, re, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

HTML = (pathlib.Path(__file__).resolve().parents[1] / "web" / "index.html").read_text(encoding="utf-8")
NIE_ZURUECK = {"mPass", "dKey"}          # Geheimnisse kommen nie zurück — dort steht ein Hinweis


def _block(start, ende):
    i = HTML.index(start)
    return HTML[i:HTML.index(ende, i)]


def test_zaehler_felder_kommen_beim_laden_zurueck():
    speichern = _block("$('mSave').onclick", "$('mMsg').textContent = 'übernehme")
    gesendet = set(re.findall(r"\$\('(\w+)'\)\.value", speichern)) - {"mMode"} - NIE_ZURUECK
    laden = _block("async function konfLaden()", "\nfunction umschalten()")
    gesetzt = set(re.findall(r"setV\('(\w+)'", laden))
    fehlt = sorted(gesendet - gesetzt)
    assert not fehlt, f"wird gespeichert, aber beim Öffnen nicht wieder eingetragen: {fehlt}"
    for f in ("mTL1", "mTL2", "mTL3", "mTH1", "mTH2", "mTH3"):
        assert f in gesetzt, f
    print("ok Zähler-Felder kommen beim Laden zurück:", len(gesendet))


def test_server_gibt_phasen_themen_zurueck():
    import app as A
    A.ctl = A.WallboxController({"interval_s": 10, "meter": {"mode": "mock"},
                                 "chargepoints": [{"id": "wb1", "type": "mock", "ip": "1.2.3.4", "name": "Wallbox"}]})
    A.ctl.save = lambda: True

    async def kein_neubau():
        return None
    A.ctl.rebuild_meter = kein_neubau
    themen = {"topic_l1": "N/1/grid/L1/power", "topic_l2": "N/1/grid/L2/power", "topic_l3": "N/1/grid/L3/power",
              "topic_home_l1": "N/1/haus/L1", "topic_home_l2": "N/1/haus/L2", "topic_home_l3": "N/1/haus/L3"}
    asyncio.run(A.api_meter_set({"mode": "mqtt", "host": "10.0.0.2", "port": "1883", **themen}))
    d = asyncio.run(A.api_meter_get())
    for k, v in themen.items():
        assert d["meter"].get(k) == v, (k, d["meter"].get(k))
    print("ok Server gibt die Phasen-Themen zurück")


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Konfig-bleibt-Tests bestanden")
