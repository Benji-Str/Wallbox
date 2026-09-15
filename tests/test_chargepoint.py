import asyncio, sys, json
from pathlib import Path
sys.path.insert(0, __file__.rsplit('/',2)[0])
import core.chargelog as CL
CL.FILE = Path(__file__).resolve().parent.parent / "chargelog_test.json"
if CL.FILE.exists(): CL.FILE.unlink()

from core.chargepoint import ChargePoint
from meter.mid import MidReading
import drivers.tuya as WT

class FakeDrv(WT.TuyaWallbox):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.dps={"1":435,"3":"charger_insert","4":8,"9":0,"14":"charge_now",
                  "18":False,"24":40,"25":0}
    def _status_sync(self):
        d=dict(self.dps); d["9"]=d["4"]*690 if d["18"] else 0
        if "fault" not in str(d["3"]): d["3"]="charger_charging" if d["18"] else d["3"]
        return d
    def _set_sync(self, dp, v): self.dps[str(dp)]=v; return True
WT.TuyaWallbox_orig = WT.TuyaWallbox

CFG = {"id":"wb1","name":"Wallbox","type":"tuya","ip":"192.168.1.8",
       "device_id":"x","local_key":"y","protocol":"3.4","phases":3,"volt":230,
       "min_a":8,"max_a":32,"dp_switch":18,"dp_current":4,"dp_power":9,
       "dp_state":3,"dp_temp":24,"dp_mode":14,"min_switch_interval_s":0,
       "charge":{"mode":"pv","einschalt_w":6000,"einschalt_delay_s":0,
                 "ausschalt_w":0,"ausschalt_delay_s":0},
       "mid_meter":{"preset":"eastron","host":"192.168.1.50"}}

async def main():
    import core.chargepoint as CP
    cp = ChargePoint(dict(CFG))
    cp.driver = FakeDrv(**{k:v for k,v in CFG.items()
                           if k not in ("id","type","charge","mid_meter")})
    meter = {"kwh": 1000.0}
    async def fake_read(): return MidReading(ok=True, energy_kwh=meter["kwh"], power_w=0)
    cp.mid.read = fake_read

    print("1) Auto steckt, kein Ueberschuss")
    a = await cp.tick(feed_in_w=500)
    print(f"   belegt {a:.0f} W | {cp.state.reason}"); assert a == 0

    print("2) Sonne kommt: 9 kW Einspeisung")
    a = await cp.tick(feed_in_w=9000)
    L = cp.live()
    print(f"   belegt {a:.0f} W -> {cp.driver.dps['4']} A, laedt={L['charging']}")
    print(f"   MID-Zaehler: {L['mid']['energy_kwh']} kWh, Start erfasst: {cp.session['meter_start']}")
    assert a == 9000 and cp.driver.dps["4"] == 13 and cp.session["meter_start"] == 1000.0

    print("3) Es laeuft, Zaehler zaehlt hoch")
    meter["kwh"] = 1004.25
    a = await cp.tick(feed_in_w=0)      # Box zieht jetzt selbst -> Einspeisung 0
    L = cp.live()
    print(f"   Sitzung {L['session_kwh']} kWh (aus MID), belegt {a:.0f} W")
    assert L["session_kwh"] == 4.25

    print("4) Auto abgesteckt -> Ladevorgang wird abgeschlossen")
    meter["kwh"] = 1007.5
    cp.driver.dps["3"] = "charger_free"
    cp.driver.dps["18"] = False
    a = await cp.tick(feed_in_w=9000)
    print(f"   belegt {a:.0f} W | {cp.state.reason}"); assert a == 0

    rows = CL.list_sessions()
    print("\n5) Ladelog:")
    for r in rows:
        print(f"   {r['kwh']} kWh in {r['minutes']} min | Modus {r['mode']} | "
              f"Quelle {r['source']} | Zaehler {r['meter_start']} -> {r['meter_end']}")
    assert len(rows) == 1
    assert rows[0]["kwh"] == 7.5 and rows[0]["source"] == "mid"
    assert rows[0]["meter_start"] == 1000.0 and rows[0]["meter_end"] == 1007.5
    print("   Summen:", CL.totals())
    print("\nALLE TESTS OK")

asyncio.run(main())
