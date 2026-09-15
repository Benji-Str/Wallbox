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
ok, grid, pv, *_ = s.lese()
print(f"  positiv=Bezug:      grid={grid} pv={pv}"); assert (ok, grid) == (True, -4500.0)

s = MqttSource(host="x", topic_grid="g", grid_sign=-1.0)
s._werte = {"g": (4500.0, time.time())}
ok, grid, *_ = s.lese()
print(f"  positiv=Einspeisung: grid={grid} (gedreht)"); assert grid == -4500.0

s = MqttSource(host="x", topic_grid="g", scale=1000.0)     # System sendet kW
s._werte = {"g": (-4.5, time.time())}
ok, grid, *_ = s.lese()
print(f"  kW-Quelle:           grid={grid}"); assert grid == -4500.0

print("\n--- Aus den Phasen zusammensetzen ---")
s = MqttSource(host="x", topic_l1="a", topic_l2="b", topic_l3="c")
s._werte = {"a": (-1000.0, time.time()), "b": (-1500.0, time.time()), "c": (-2000.0, time.time())}
ok, grid, *_ = s.lese()
print(f"  L1+L2+L3 -> grid={grid}"); assert (ok, grid) == (True, -4500.0)

print("\n--- Veralten: lieber pausieren als mit eingefrorenem Wert regeln ---")
s = MqttSource(host="x", topic_grid="g", stale_s=30)
s._werte = {"g": (-4500.0, time.time() - 31)}
ok, grid, *_ = s.lese()
print(f"  31 s alt bei stale_s=30 -> ok={ok}"); assert ok is False
s._werte = {"g": (-4500.0, time.time() - 5)}
ok, grid, *_ = s.lese()
print(f"   5 s alt                -> ok={ok} grid={grid}"); assert ok is True

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
