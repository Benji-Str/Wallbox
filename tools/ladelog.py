#!/usr/bin/env python3
"""Eine Ladung mitschreiben — mit ABGESCHALTETER Steuerung.

Der Versuch, der alles entscheidet: Wenn die Software nichts anfasst und das
Auto trotzdem nicht laedt, liegt es nicht an der Software. Laedt es dagegen
sauber, ist unsere Regelung schuld — dann wissen wir, wo zu suchen ist.

    systemctl stop wallbox                       # WICHTIG: erst den Dienst
    .venv/bin/python3 tools/ladelog.py           # bis Strg+C
    .venv/bin/python3 tools/ladelog.py --minuten 15

Dann die Karte vorhalten, notfalls ab- und wieder anstecken. Alles wird
mitgeschrieben und zum Verschicken in eine Datei gelegt.

**Warum der Dienst aus muss:** Eine Tuya-Wallbox nimmt im lokalen Netz nur
EINE Verbindung an. Laeuft der Dienst, streiten sich beide darum — dann gehen
Befehle verloren, und man misst nicht die Anlage, sondern den Streit. Dieses
Werkzeug prueft das und weigert sich, wenn der Dienst antwortet.

Es wird ausschliesslich GELESEN. Kein einziger Schreibzugriff, keine
Schaltbefehle — nur zusehen.
"""
from __future__ import annotations
import argparse, socket, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.paths import DATA, venv_hinweis                       # noqa: E402
from drivers.tuya import CP_TEXT, _stoerungen                   # noqa: E402


def dienst_laeuft(port=8081) -> bool:
    """Antwortet auf dem Port noch etwas? Dann haelt der Dienst die Verbindung."""
    with socket.socket() as s:
        s.settimeout(0.7)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False


def lies_cfg() -> dict:
    import json
    for p in (Path(DATA) / "wallbox.json", Path("config.example.json")):
        if p.exists():
            for c in json.loads(p.read_text(encoding="utf-8")).get("chargepoints", []):
                if c.get("device_id") and c.get("local_key"):
                    return c
    sys.exit("Keine Wallbox mit device_id und local_key in der Konfiguration.")


def zahl(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def bild(dps: dict, c: dict) -> dict:
    """Die Datenpunkte auf das eindampfen, worauf es beim Laden ankommt."""
    amp = zahl(dps.get(str(c.get("dp_current", 4))))
    watt = zahl(dps.get(str(c.get("dp_power", 9))))
    cp = dps.get(str(c.get("dp_connection", 13)))
    return {
        "an": bool(dps.get(str(c.get("dp_switch", 18)))),
        "zustand": dps.get(str(c.get("dp_state", 3))),
        "cp": cp,
        "cp_text": CP_TEXT.get(cp, cp or "?"),
        "amp": amp, "watt": watt,
        "modus": dps.get(str(c.get("dp_mode", 14))),
        "stoerungen": _stoerungen(dps.get(str(c.get("dp_fault", 10)))),
    }


def kennzeichen(b: dict) -> tuple:
    """Leistung in 100-W-Stufen: sonst steht bei jedem Messrauschen eine Zeile."""
    return (b["an"], b["zustand"], b["cp"], b["amp"], b["modus"],
            None if b["watt"] is None else round(b["watt"] / 100.0),
            tuple(b["stoerungen"]))


def zeile(b: dict) -> str:
    return (f"{'SCHUETZ EIN' if b['an'] else 'Schuetz aus':<12} "
            f"{(b['watt'] or 0):>6.0f} W {(b['amp'] or 0):>4.0f} A  "
            f"{str(b['zustand'] or '?'):<18} {b['cp_text']}"
            + ("  STOERUNG: " + ", ".join(b["stoerungen"]) if b["stoerungen"] else ""))


def main(argv=None):
    p = argparse.ArgumentParser(description="Eine Ladung ohne Steuerung mitschreiben")
    p.add_argument("--minuten", type=float, default=0, help="0 = bis Strg+C")
    p.add_argument("--takt", type=float, default=2.0, help="Sekunden zwischen den Abfragen")
    p.add_argument("--trotzdem", action="store_true",
                   help="auch bei laufendem Dienst (nicht empfohlen)")
    a = p.parse_args(argv)

    try:
        import tinytuya
    except ImportError:
        sys.exit(venv_hinweis("tinytuya"))

    if dienst_laeuft() and not a.trotzdem:
        print("Der Dienst laeuft noch — und die Box nimmt nur EINE Verbindung an.")
        print("Solange beide zugreifen, messen wir den Streit und nicht die Anlage.")
        print()
        print("  systemctl stop wallbox")
        print(f"  {sys.executable} {' '.join(sys.argv)}")
        print()
        print("Hinterher wieder einschalten:  systemctl start wallbox")
        return 1

    c = lies_cfg()
    d = tinytuya.Device(c["device_id"], c["ip"], c["local_key"])
    d.set_version(float(c.get("protocol", 3.4)))
    d.set_socketTimeout(5)
    d.set_socketPersistent(True)

    datei = Path(DATA) / f"ladelog-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    mit = datei.open("w", encoding="utf-8")

    def sag(text=""):
        print(text, flush=True)
        mit.write(text + "\n")
        mit.flush()

    sag(f"Ladelog {time.strftime('%d.%m.%Y %H:%M:%S')} · Box {c['ip']}")
    sag("Die Steuerung ist aus. Es wird nur gelesen, nichts geschaltet.")
    sag("Jetzt die Karte vorhalten — notfalls abstecken, 30 s warten, anstecken.")
    sag()

    letztes, letzte_zeile = None, 0.0
    max_watt, lud_ab, lud_s = 0.0, None, 0.0
    ende = time.time() + a.minuten * 60 if a.minuten else None
    try:
        while ende is None or time.time() < ende:
            st = d.status() or {}
            dps = {str(k): v for k, v in (st.get("dps") or {}).items()}
            if not dps:
                sag(f"{time.strftime('%H:%M:%S')}  (keine Antwort — "
                    f"{st.get('Error', 'leere Antwort')})")
                time.sleep(a.takt)
                continue
            b = bild(dps, c)
            watt = b["watt"] or 0.0
            max_watt = max(max_watt, watt)
            if watt > 200:
                lud_ab = lud_ab or time.time()
                lud_s += a.takt
            jetzt = kennzeichen(b)
            if jetzt != letztes or time.time() - letzte_zeile >= 30:
                sag(f"{time.strftime('%H:%M:%S')}  {zeile(b)}")
                letztes, letzte_zeile = jetzt, time.time()
            time.sleep(a.takt)
    except KeyboardInterrupt:
        sag()

    sag()
    sag("── Ergebnis ──")
    if max_watt > 200:
        sag(f"  Das Auto hat geladen: bis {max_watt:.0f} W, "
            f"rund {lud_s / 60:.0f} min lang.")
        sag("  Damit liegt es NICHT an der Box und nicht am Auto, sondern an "
            "der Steuerung.")
    else:
        sag(f"  Das Auto hat NICHT geladen (hoechstens {max_watt:.0f} W) — "
            "obwohl die Steuerung aus war.")
        sag("  Damit liegt es nicht an der Software. Zu pruefen sind dann: "
            "Ladeplan und Ladelimit im Auto, die Kartenpflicht an der Box, "
            "das Kabel.")
    sag(f"  Diese Datei verschicken:  {datei}")
    sag()
    sag("Steuerung wieder einschalten:  systemctl start wallbox")
    mit.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
