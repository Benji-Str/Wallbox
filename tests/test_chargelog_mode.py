import asyncio, sys
from pathlib import Path
sys.path.insert(0, __file__.rsplit('/',2)[0])
import core.chargelog as CL
CL.FILE = Path(__file__).resolve().parent.parent / "chargelog_test.json"
if CL.FILE.exists(): CL.FILE.unlink()
from core.chargepoint import ChargePoint

CFG = {"id":"wb1","type":"mock","ip":"mock","name":"WB","phases":3,"volt":230,
       "min_a":8,"max_a":32,"min_switch_interval_s":0,
       "mid_meter":{"mode":"mock","start_kwh":100.0},
       "charge":{"mode":"sofort","sofort_a":16}}

async def main():
    cp = ChargePoint(dict(CFG))
    def spule_vor(sek):
        """Simulierte Uhr vorstellen — sonst laedt der Test in Millisekunden."""
        cp.driver._t -= sek
        if cp.mid._mock_t: cp.mid._mock_t -= sek
        cp.session and cp.session.__setitem__("start", cp.session["start"] - sek)

    await cp.tick(0)
    for _ in range(3):
        spule_vor(300)
        await cp.tick(0)
    print("laedt in Modus:", cp.state.mode, "|", cp.state.reason)
    cp.set_mode("stop")                      # Nutzer beendet ueber die Oberflaeche
    await cp.tick(0)
    r = CL.list_sessions()[0]
    print(f"Ladelog-Eintrag -> Modus '{r['mode']}' (erwartet 'sofort')")
    assert r["mode"] == "sofort", r
    print("OK")

asyncio.run(main())
