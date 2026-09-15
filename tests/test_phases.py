"""Phasenerkennung: die Box kann nicht umschalten, also muss die Software
sagen, wie viele Phasen wirklich laden — sonst rechnet sie Watt falsch."""
import asyncio, sys
sys.path.insert(0, __file__.rsplit('/',2)[0])
from core.chargepoint import ChargePoint

def mk(phasen):
    return ChargePoint({"id":"wb1","type":"mock","ip":"mock","name":"WB",
                        "phases":phasen,"volt":230,"min_a":8,"max_a":32,
                        "min_switch_interval_s":0,
                        "charge":{"mode":"sofort","sofort_a":16}})

async def main():
    for n in (1, 2, 3):
        cp = mk(n)
        await cp.tick(0)
        cp.driver._t -= 30
        await cp.tick(0)
        L = cp.live()
        print(f"  {n}-phasig: {L['power_w']:.0f} W | L1/L2/L3 = "
              f"{L['phase_a']}/{L['phase_b']}/{L['phase_c']} A | "
              f"aktiv {L['phases_active']} von {L['phases_cfg']} | "
              f"{L['min_w']}-{L['max_w']} W")
        assert L["phases_cfg"] == n
        assert L["phases_active"] == n, (n, L["phases_active"])
        assert L["min_w"] == 8 * n * 230

    print("\n  Regelbereich je Phasenzahl:")
    for n in (1, 2, 3):
        cp = mk(n)
        print(f"    {n} Phase(n): {cp.driver.min_w}-{cp.driver.max_w} W, "
              f"{cp.driver.granularity_w} W je Ampere")

    print("\n  Ohne Ladung keine Aussage (0 waere irrefuehrend):")
    cp = mk(3); await cp.tick(0)
    cp.set_mode("stop")
    cp.driver._t -= 30; await cp.tick(0)          # schaltet ab, zieht noch
    print(f"    im Abschalt-Takt: {cp.live()['phases_active']} "
          f"(richtig — sie zieht ja noch)")
    cp.driver._t -= 30; await cp.tick(0)          # jetzt ist sie aus
    L = cp.live()
    print(f"    danach: {L['power_w']:.0f} W -> phases_active = {L['phases_active']}")
    assert L["phases_active"] is None
    print("\nALLE TESTS OK")

asyncio.run(main())
