"""MQTT-Zaehler: Nutzlast auswerten, Vorzeichen, Veralten — ohne Broker."""
import sys, time
sys.path.insert(0, __file__.rsplit('/',2)[0])
from meter.mqtt import parse_payload, MqttSource

print("--- Nutzlast auswerten ---")
faelle = [
    (b"1234.5", "", 1234.5, "nackte Zahl"),
    ("-987", "", -987.0, "negative Zahl"),
    (b'{"value": 2500}', "value", 2500.0, "Victron-Stil"),
    (b'{"data":{"p":1750.25}}', "data.p", 1750.25, "verschachtelt"),
    (b'{"value": 42}', "", 42.0, "JSON ohne json_key -> gaengige Schluessel"),
    (b'{"power": 900}', "", 900.0, "Schluessel power"),
    (b"", "", None, "leer"),
    (b"ON", "", None, "keine Zahl"),
    (b'{"value":1}', "fehlt", None, "Schluessel nicht vorhanden"),
    (b'kein json', "value", None, "kaputtes JSON mit json_key"),
]
for nutz, key, soll, was in faelle:
    ist = parse_payload(nutz, key)
    print(f"  {was:42s} -> {ist}")
    assert ist == soll, (was, ist, soll)

print("\n--- Vorzeichen und Skalierung ---")
s = MqttSource(host="x", topic_grid="g", topic_pv="pv", grid_sign=1.0, scale=1.0)
s._werte = {"g": (-4500.0, time.time()), "pv": (6000.0, time.time())}
d = s.lese()
print(f"  positiv=Bezug:       grid={d['grid_w']} pv={d['pv_w']}")
assert (d["ok"], d["grid_w"]) == (True, -4500.0)

s = MqttSource(host="x", topic_grid="g", grid_sign=-1.0)
s._werte = {"g": (4500.0, time.time())}
d = s.lese()
print(f"  positiv=Einspeisung: grid={d['grid_w']} (gedreht)"); assert d["grid_w"] == -4500.0

s = MqttSource(host="x", topic_grid="g", scale=1000.0)     # System sendet kW
s._werte = {"g": (-4.5, time.time())}
d = s.lese()
print(f"  kW-Quelle:           grid={d['grid_w']}"); assert d["grid_w"] == -4500.0

print("\n--- Aus den Phasen zusammensetzen ---")
s = MqttSource(host="x", topic_l1="a", topic_l2="b", topic_l3="c")
s._werte = {"a": (-1000.0, time.time()), "b": (-1500.0, time.time()), "c": (-2000.0, time.time())}
d = s.lese()
print(f"  L1+L2+L3 -> grid={d['grid_w']}"); assert (d["ok"], d["grid_w"]) == (True, -4500.0)

print("\n--- Veralten: lieber pausieren als mit eingefrorenem Wert regeln ---")
s = MqttSource(host="x", topic_grid="g", stale_s=30)
s._werte = {"g": (-4500.0, time.time() - 31)}
d = s.lese()
print(f"  31 s alt bei stale_s=30 -> ok={d['ok']}"); assert d["ok"] is False
s._werte = {"g": (-4500.0, time.time() - 5)}
d = s.lese()
print(f"   5 s alt                -> ok={d['ok']} grid={d['grid_w']}"); assert d["ok"] is True

print("\n--- Ohne Broker/Thema kein Start, aber klare Meldung ---")
s = MqttSource(host="", topic_grid="g")
print("  ", s.start(), "|", s.letzter_fehler); assert s.start() is False

print("\nALLE TESTS OK")

print("\n--- Zaehler gibt host/port weiter (waren zuvor verschluckt) ---")
from meter.grid import MecMeter
m = MecMeter(mode="mqtt", host="10.0.0.7", port=1884,
             topic_grid="haus/netz", json_key="value", grid_sign=-1)
q = m.mqtt()
print(f"  MqttSource: host={q.cfg.host} port={q.cfg.port} thema={q.cfg.topic_grid}")
assert (q.cfg.host, q.cfg.port) == ("10.0.0.7", 1884)
assert q.cfg.topic_grid == "haus/netz" and q.cfg.grid_sign == -1
assert q.letzter_fehler != "Broker oder Thema fehlt", q.letzter_fehler
q.stop()
print("  OK")

print("\n--- Alle Werte aus MQTT ---")
s = MqttSource(host="x", topic_grid="g", topic_pv="pv", topic_battery="bat",
               topic_soc="soc", topic_home="home", grid_sign=-1, scale=1)
jetzt = time.time()
s._werte = {"g": (4000.0, jetzt), "pv": (9000.0, jetzt), "bat": (2500.0, jetzt),
            "soc": (78.0, jetzt), "home": (1500.0, jetzt)}
d = s.lese()
print(f"  grid={d['grid_w']} pv={d['pv_w']} speicher={d['battery_w']} "
      f"soc={d['soc_pct']}% haus={d['home_w']}")
assert d["grid_w"] == -4000.0 and d["battery_w"] == 2500.0
assert d["soc_pct"] == 78.0            # Ladestand wird NICHT skaliert
assert d["home_w"] == 1500.0

print("\n--- Unbekannt ist nicht null ---")
s = MqttSource(host="x", topic_grid="g")
s._werte = {"g": (-3000.0, time.time())}
d = s.lese()
print(f"  ohne Speicher-Thema: battery_w={d['battery_w']} soc={d['soc_pct']}")
assert d["battery_w"] is None and d["soc_pct"] is None

print("\n--- Speicher-Vorzeichen drehen ---")
s = MqttSource(host="x", topic_grid="g", topic_battery="b", battery_sign=-1)
s._werte = {"g": (0.0, time.time()), "b": (2000.0, time.time())}
print(f"  positiv=Entladen -> battery_w={s.lese()['battery_w']}")
assert s.lese()["battery_w"] == -2000.0

print("\n--- Zuordnungsvorschlag der Themensuche ---")
from meter.mqtt import _passt
faelle = [("N/123/system/0/Ac/Grid/L1/Power", -4500, "grid"),
          ("solar/pv/power", 9000, "pv"),
          ("victron/battery/soc", 78, "soc"),
          ("haus/speicher/leistung", 2500, "battery"),
          ("energy/home/consumption", 1500, "home"),
          ("irgendwas/anderes", 5, ""),
          ("shellies/relay/0/power", None, "")]
for t, z, soll in faelle:
    ist = _passt(t, z)
    print(f"  {t:38s} -> {ist or '(kein Vorschlag)'}")
    assert ist == soll, (t, ist, soll)

print("\nERWEITERTE TESTS OK")

print("\n--- Nur Phasenwerte, keine Summe ---")
jetzt = time.time()
s = MqttSource(host="x", topic_l1="n/l1", topic_l2="n/l2", topic_l3="n/l3",
               topic_home_l1="v/l1", topic_home_l2="v/l2", topic_home_l3="v/l3")
s._werte = {"n/l1": (-1200.0, jetzt), "n/l2": (-1500.0, jetzt), "n/l3": (-1800.0, jetzt),
            "v/l1": (400.0, jetzt),  "v/l2": (350.0, jetzt),  "v/l3": (250.0, jetzt)}
d = s.lese()
print(f"  Netz  = {d['grid_w']} W  (aus -1200/-1500/-1800)")
print(f"  Haus  = {d['home_w']} W  (aus 400/350/250)")
assert d["ok"] and d["grid_w"] == -4500.0 and d["home_w"] == 1000.0

print("\n--- Fehlt eine Phase, gibt es keine Teilsumme ---")
s._werte.pop("v/l2")
d = s.lese()
print(f"  Haus mit fehlender L2 -> {d['home_w']} (nicht 650)")
assert d["home_w"] is None
s._werte.pop("n/l3")
d = s.lese()
print(f"  Netz mit fehlender L3 -> ok={d['ok']} (Netz ist Pflicht)")
assert d["ok"] is False

print("\n--- Summe hat Vorrang vor den Phasen ---")
s = MqttSource(host="x", topic_grid="n/sum", topic_l1="n/l1", topic_l2="n/l2", topic_l3="n/l3")
s._werte = {"n/sum": (-4000.0, jetzt), "n/l1": (-1.0, jetzt),
            "n/l2": (-1.0, jetzt), "n/l3": (-1.0, jetzt)}
print(f"  Summe -4000 trotz Phasen -3 -> {s.lese()['grid_w']}")
assert s.lese()["grid_w"] == -4000.0

print("\n--- Phase aus dem Themennamen ---")
from meter.mqtt import phase_aus_thema
for t, soll in (("N/123/system/0/Ac/Grid/L1/Power","l1"),
                ("haus/verbrauch_l2","l2"),
                ("shelly/emeter/phase3/power","l3"),
                ("haus/netz/gesamt","")):
    ist = phase_aus_thema(t)
    print(f"  {t:42s} -> {ist or '(keine)'}"); assert ist == soll, t

print("\nPHASEN-TESTS OK")
