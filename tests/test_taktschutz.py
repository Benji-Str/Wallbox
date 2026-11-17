"""Der Taktschutz darf einen Menschen nicht aussperren — und nie stumm sein.

Der Fehler aus dem Betrieb: In der Oberflaeche auf „Sofort" gedrueckt, nichts
passiert. Die Box laedt erst, wenn man die Karte vorhaelt. Im Code lag es
daran, dass `resume()` innerhalb der Schonzeit einfach `False` zurueckgibt —
und niemand den Rueckgabewert ansieht. Die Oberflaeche zeigte weiter
„Sofortladen 16 A", waehrend an der Box nie ein Befehl ankam.

Zwei Regeln halten das jetzt fest:
  1. Ein Moduswechsel von Hand hebt die Sperre einmal auf.
  2. Solange gesperrt ist, steht die Restzeit in `live()` — und die
     Oberflaeche sagt, dass der Schuetz aus ist, obwohl geladen werden soll.
"""
import asyncio, sys
from pathlib import Path
sys.path.insert(0, __file__.rsplit('/', 2)[0])
import core.chargelog as CL
CL.FILE = Path(__file__).resolve().parent.parent / "chargelog_test.json"
if CL.FILE.exists():
    CL.FILE.unlink()

from core.chargepoint import ChargePoint
import drivers.tuya as WT

SPERRE = 300


class FakeDrv(WT.TuyaWallbox):
    """Box, die jeden Schreibzugriff mitschreibt."""

    def __init__(self, **kw):
        super().__init__(**kw)
        self.dps = {"1": 435, "3": "charger_insert", "4": 8, "9": 0,
                    "14": "charge_now", "18": False, "24": 40}
        self.geschrieben = []

    def _status_sync(self):
        d = dict(self.dps)
        d["9"] = d["4"] * 690 if d["18"] else 0
        if d["18"]:
            d["3"] = "charger_charging"
        return d

    def _set_sync(self, dp, v):
        self.dps[str(dp)] = v
        self.geschrieben.append((int(dp), v))
        return True


def mk(**extra):
    cfg = {"id": "wb1", "name": "Wallbox", "type": "tuya", "ip": "192.168.1.8",
           "device_id": "x", "local_key": "y", "protocol": "3.4", "phases": 3,
           "volt": 230, "min_a": 8, "max_a": 32, "dp_switch": 18, "dp_current": 4,
           "dp_power": 9, "dp_state": 3, "dp_temp": 24, "dp_mode": 14,
           "min_switch_interval_s": SPERRE,
           "charge": {"mode": "stop", "sofort_a": 16}}
    cfg.update(extra)
    cp = ChargePoint(cfg, log=lambda *a: None)
    cp.driver.__class__ = FakeDrv
    FakeDrv.__init__(cp.driver, ip="192.168.1.8", device_id="x", local_key="y",
                     phases=3, volt=230, min_a=8, max_a=32,
                     min_switch_interval_s=SPERRE, dp_mode=14)
    return cp


def lauf(cp, n=1):
    for _ in range(n):
        asyncio.run(cp.tick(0.0))


print("--- Sofort von Hand startet, obwohl gerade geschaltet wurde ---")
cp = mk()
lauf(cp)                                   # ein Takt im Modus stop
cp.driver._last_switch = __import__("time").time()     # eben erst geschaltet
assert cp.driver.sperre_rest_s() > 290, cp.driver.sperre_rest_s()
cp.set_mode("sofort")
rest = cp.driver.sperre_rest_s()
assert 0 < rest <= WT.HAND_SPERRE_S, f"von Hand nur noch {rest} s statt {SPERRE}"
cp.driver._last_switch -= WT.HAND_SPERRE_S     # die zehn Sekunden vergehen
cp.driver.geschrieben.clear()
lauf(cp, 2)          # Takt 1 schaltet, Takt 2 liest den neuen Zustand
assert (18, True) in cp.driver.geschrieben, cp.driver.geschrieben
assert cp.live()["switch_on"] is True
print(f"  geschrieben: {cp.driver.geschrieben} | {cp.live()['reason']}")

print("--- Die Regelung selbst bleibt gebremst ---")
cp = mk()
lauf(cp)
cp.set_mode("sofort")
lauf(cp, 2)                                # schaltet ein, Sperre laeuft an
assert cp.live()["switch_on"] is True
cp.ctrl.cfg.mode = "stop"                  # wie die Regelung: ohne set_mode
cp.driver.geschrieben.clear()
lauf(cp)
assert (18, False) not in cp.driver.geschrieben, "Taktschutz haelt die Regelung"
assert cp.live()["sperre_s"] > 0
print(f"  Restsperre {cp.live()['sperre_s']} s, kein Ausschaltbefehl")

print("--- Ohne vorheriges Schalten geht es sofort ---")
cp = mk()
cp.set_mode("sofort")
assert cp.driver.sperre_rest_s() == 0, "nichts zu schuetzen, nichts zu warten"
lauf(cp, 2)
assert cp.live()["switch_on"] is True
print("  sofort geschaltet")

print("--- Derselbe Modus nochmal hebt nichts auf ---")
cp = mk()
cp.set_mode("sofort")
lauf(cp, 2)
rest_vorher = cp.live()["sperre_s"]
cp.set_mode("sofort")
assert cp.live()["sperre_s"] >= rest_vorher - 1, "kein Freibrief durch Doppelklick"
print(f"  Restsperre bleibt {cp.live()['sperre_s']} s")

print("--- Will laden, Schuetz aus: das steht in live() ---")
cp = mk()
cp.set_mode("sofort")
lauf(cp, 2)
assert cp.live()["nicht_geschaltet_s"] == 0, "im Normalfall kein Alarm"
cp.driver.dps["18"] = False                # Box nimmt den Befehl nicht an
cp.driver._last_switch = __import__("time").time()   # und der Taktschutz sperrt
lauf(cp, 2)
l = cp.live()
assert l["charging"] and not l["switch_on"], l
assert cp._stumm_seit, "der Zeitpunkt wurde gemerkt"
cp._stumm_seit -= 45                       # 45 s spaeter, immer noch nichts
l = cp.live()
assert 44 <= l["nicht_geschaltet_s"] <= 46, l["nicht_geschaltet_s"]
print(f"  charging={l['charging']} switch_on={l['switch_on']} "
      f"nicht_geschaltet_s={l['nicht_geschaltet_s']} sperre_s={l['sperre_s']}")

print("--- Geht der Schuetz zu, ist der Alarm sofort weg ---")
cp.driver.dps["18"] = True
lauf(cp)
assert cp.live()["nicht_geschaltet_s"] == 0, cp.live()["nicht_geschaltet_s"]
print("  zurueckgesetzt")

print("--- Die Oberflaeche zeigt es auch ---")
ui = (Path(__file__).resolve().parent.parent / "web" / "index.html").read_text("utf-8")
assert "c.nicht_geschaltet_s > 30" in ui
assert "Kartenpflicht" in ui and "Taktschutz sperrt" in ui
print("  Hinweis auf Kartenpflicht und Taktschutz vorhanden")

print("\nAlle Taktschutz-Tests bestanden.")
