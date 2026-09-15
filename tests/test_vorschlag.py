"""Automatische Zuordnung — gegen die echte Themenliste der Anlage."""
import sys
sys.path.insert(0, __file__.rsplit('/',2)[0])
from meter.mqtt import vorschlag, bester_vorschlag

# genau das, was die Themensuche am Broker 192.168.1.60 geliefert hat
ECHT = [
 ("Ac/Grid/L1/Power",-2.9),("Ac/Grid/L2/Power",-1.3),("Ac/Grid/L3/Power",2),
 ("Batterie V",59.099998474121094),("Entladen",-1207),
 ("Fronius symo3 Ost",0),("Fronius symo3,7 Ost",0),
 ("GridL1",-12.9),("GridL2",-1.3),("GridL3",2),("Haus_West",0),
 ("Laden/Entladen in A",-22.5),("Laden/entladen",-22.5),("Soc",72),("SüdPV",0),
 ("VerbrauchL1",876),("VerbrauchL2",186),("VerbrauchL3",137),
 ("gridem24",-6.8),("mining/balance",0),("mining/hashrate/24h",0),
 ("mining/pool/workers",0),("mining/reward/today",0),
 ("mppt Osten Links",0),("mppt Osten rechts",0),("soc",59),
 ("solar/totalPower",0),
 ("tasmota/discovery/3494548368A9/sensors",None),
 ("tele/heater/LWT",None),("tele/tempWZ/SENSOR",None),
 ("kwh in Batterie",None),
]
THEMEN = [{"topic":t,"zahl":z} for t,z in ECHT]

print("=== Kandidaten je Feld (nach Eignung) ===")
v = vorschlag(THEMEN)
for feld in ("grid","home","pv","battery","soc"):
    print(f"\n{feld}:")
    for slot in ("summe","l1","l2","l3"):
        liste = v[feld][slot]
        if liste:
            print(f"  {slot:6s} " + " · ".join(
                f"{x['topic']} ({x['punkte']})" for x in liste[:3]))

print("\n=== Bester Vorschlag ===")
b = bester_vorschlag(THEMEN)
for k in ("topic_grid","topic_l1","topic_l2","topic_l3","topic_home",
          "topic_home_l1","topic_home_l2","topic_home_l3",
          "topic_pv","topic_battery","topic_soc"):
    print(f"  {k:<16} = {b[k] or '(leer)'}")

print("\n=== Pruefungen ===")
# Netz: Phasen statt Summe, und die Victron-Systemwerte
assert b["topic_l1"].startswith("Ac/Grid/L1") or b["topic_l1"] == "GridL1", b["topic_l1"]
assert b["topic_l2"] and b["topic_l3"], "Phasen unvollstaendig"
assert b["topic_grid"] == "", "bei vollstaendigen Phasen kein Summenthema"
print("  Netz  -> drei Phasen, kein Summenthema        ok")

assert b["topic_home_l1"] == "VerbrauchL1"
assert b["topic_home_l2"] == "VerbrauchL2"
assert b["topic_home_l3"] == "VerbrauchL3"
print("  Haus  -> VerbrauchL1/L2/L3                    ok")

assert b["topic_battery"] == "Entladen", b["topic_battery"]
print("  Speicher -> 'Entladen', nicht 'Batterie V'    ok")
assert "Batterie V" not in str(b.values())
assert "in A" not in b["topic_battery"]

assert b["topic_soc"] in ("Soc","soc"), b["topic_soc"]
print(f"  Ladestand -> {b['topic_soc']}                            ok")

assert b["topic_pv"] == "solar/totalPower", b["topic_pv"]
print("  PV -> solar/totalPower (Gesamtwert)           ok")

# Mining und Tasmota duerfen nirgends auftauchen
alle = " ".join(str(x) for x in b.values())
for muell in ("mining/","tasmota/","tele/"):
    assert muell not in alle, muell
print("  Mining/Tasmota/tele ausgeschlossen            ok")

print("\n=== Ohne brauchbare Themen ===")
leer = bester_vorschlag([{"topic":"irgendwas","zahl":5}])
assert all(x == "" for x in leer.values())
print("  nichts Passendes -> alle Felder leer          ok")

print("\nALLE TESTS OK")
