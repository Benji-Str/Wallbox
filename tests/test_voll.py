"""Erkennt die Steuerung, wann das Auto von selbst aufhoert?

Das ist der einzige Zeitpunkt, an dem sich ueber die Akkugroesse etwas sagen
laesst — und der haeufigste Weg, sich dabei zu vertun: eine kurze Pause des
Fahrzeugs als "voll" zu werten. Getestet wird gegen den echten Treiberpfad;
nur die Antwort der Box ist vorgegeben.
"""
import sys, pathlib, asyncio, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from core.chargepoint import ChargePoint

UHR = [10_000.0]
_ECHT = time.time


def punkt(**kw):
    cfg = {"id": "t1", "type": "mock", "ip": "1.2.3.4",
           "voll_w": 200, "voll_delay_s": 300,
           "charge": {"mode": "sofort", "sofort_a": 16}}
    cfg.update(kw)
    cp = ChargePoint(cfg, log=lambda *a: None)
    zustand = {"w": 0.0, "plugged": True}
    d = cp.driver

    def antwort():
        w = zustand["w"] if zustand["plugged"] else 0.0
        return {str(d.dp_switch): True, str(d.dp_current): 16,
                str(d.dp_power): round(w),
                str(d.dp_state): ("charger_charging" if w > 50 else
                                  "charger_insert" if zustand["plugged"]
                                  else "charger_free"),
                str(d.dp_temp): 25, str(d.dp_mode): "charge_now",
                "13": ("controlpi_6v_pwm" if w > 50 else
                       "controlpi_9v_pwm" if zustand["plugged"] else "controlpi_12v"),
                "10": 0, "25": 0}
    d._status_sync = antwort
    return cp, zustand


def takt(cp, zustand, w, plugged=True):
    zustand["w"], zustand["plugged"] = w, plugged
    asyncio.run(cp.tick(0.0, True))


def mit_uhr(fn):
    """Die Uhr anhalten — sonst dauerte ein Test fuenf echte Minuten."""
    def lauf():
        UHR[0] = 10_000.0
        time.time = lambda: UHR[0]
        try:
            fn()
        finally:
            time.time = _ECHT
    lauf.__name__ = fn.__name__
    return lauf


@mit_uhr
def test_kurze_pause_ist_nicht_voll():
    cp, z = punkt()
    takt(cp, z, 6900)
    UHR[0] += 120; takt(cp, z, 0)      # zwei Minuten Pause
    UHR[0] += 120; takt(cp, z, 0)      # vier Minuten — noch unter der Grenze
    assert cp._voll is False, "vier Minuten Pause duerfen nicht 'voll' heissen"
    UHR[0] += 30; takt(cp, z, 6900)    # Auto laedt weiter
    assert cp._voll is False and cp._leerlauf_seit == 0
    print("ok kurze Pause ist nicht voll")


@mit_uhr
def test_langer_stillstand_ist_voll():
    cp, z = punkt()
    takt(cp, z, 6900)
    UHR[0] += 10;  takt(cp, z, 0)
    UHR[0] += 400; takt(cp, z, 0)      # ueber der Verzoegerung
    assert cp._voll is True
    print("ok langer Stillstand am Kabel heisst voll")


@mit_uhr
def test_abstecken_setzt_zurueck():
    cp, z = punkt()
    takt(cp, z, 6900)
    UHR[0] += 10;  takt(cp, z, 0)
    UHR[0] += 400; takt(cp, z, 0)
    assert cp._voll is True
    UHR[0] += 10; takt(cp, z, 0, plugged=False)
    assert cp._voll is False, "nach dem Abstecken gilt der Befund nicht mehr"
    print("ok Abstecken setzt die Erkennung zurueck")


@mit_uhr
def test_schwelle_gilt_nicht_fuer_kleine_restlast():
    """Manche Fahrzeuge ziehen im Stand ein paar hundert Watt fuer die
    Klimatisierung. Das ist kein Laden."""
    cp, z = punkt()
    takt(cp, z, 6900)
    UHR[0] += 10;  takt(cp, z, 150)    # unter voll_w = 200
    UHR[0] += 400; takt(cp, z, 150)
    assert cp._voll is True
    print("ok kleine Restlast gilt als Stillstand")


@mit_uhr
def test_ladevorgang_wird_als_voll_protokolliert():
    import core.chargelog as cl
    gemerkt, alt = [], cl.append_session
    cl.append_session = lambda cpid, s: gemerkt.append(s)
    try:
        cp, z = punkt()
        takt(cp, z, 6900)
        UHR[0] += 600; takt(cp, z, 6900)
        UHR[0] += 10;  takt(cp, z, 0)
        UHR[0] += 400; takt(cp, z, 0)
        assert gemerkt, "der Vorgang haette abgeschlossen werden muessen"
        assert gemerkt[-1]["ende"] == "voll", gemerkt[-1]
        print("ok der Vorgang landet mit Vermerk 'voll' im Ladelog")
    finally:
        cl.append_session = alt


@mit_uhr
def test_abgestecktes_ende_heisst_nicht_voll():
    import core.chargelog as cl
    gemerkt, alt = [], cl.append_session
    cl.append_session = lambda cpid, s: gemerkt.append(s)
    try:
        cp, z = punkt()
        takt(cp, z, 6900)
        UHR[0] += 600; takt(cp, z, 6900)
        UHR[0] += 10;  takt(cp, z, 0, plugged=False)
        assert gemerkt and gemerkt[-1]["ende"] == "abgesteckt", gemerkt[-1]
        print("ok abgesteckt wird nicht als voll gewertet")
    finally:
        cl.append_session = alt


for name, fn in sorted(list(globals().items())):
    if name.startswith("test_"):
        fn()
print("\nalle Voll-Tests bestanden")
