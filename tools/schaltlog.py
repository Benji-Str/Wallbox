#!/usr/bin/env python3
"""Mitschreiben, was beim Schalten passiert — Sekunde fuer Sekunde.

    python3 tools/schaltlog.py                      # bis Strg+C
    python3 tools/schaltlog.py --start --sekunden 240
    python3 tools/schaltlog.py --stop

Ausgegeben wird je Sekunde eine Zeile, aber nur wenn sich etwas geaendert hat —
sonst alle 30 s eine Standzeile, damit man sieht, dass es noch laeuft. Jeder
Schaltvorgang wird eingerueckt dazwischengesetzt, mit Grund und mit dem, was das
Fahrzeug in diesem Moment meldete. Alles landet zusaetzlich in einer Datei, die
man verschicken kann.

**Gefragt wird die eigene Schnittstelle, nicht die Box.** Das ist kein Umweg,
sondern der einzige richtige Weg: Eine Tuya-Wallbox nimmt im lokalen Netz
**eine** Verbindung an. Laeuft `dp_dump.py --watch` mit, waehrend der Dienst
regelt, streiten sich beide darum — und dann scheitern Schaltbefehle, weil
mitgelesen wird. Genau deshalb braucht es dieses Werkzeug: Es hoert dort zu, wo
schon alles zusammenlaeuft, und stoert die Box nicht.

Braucht nichts als die Standardbibliothek und laeuft mit dem System-Python.
"""
from __future__ import annotations
import argparse, json, sys, time, urllib.error, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BASIS = "http://127.0.0.1:8081"


def hole(pfad: str, daten: dict | None = None):
    rumpf = None if daten is None else json.dumps(daten).encode()
    kopf = {"Content-Type": "application/json"} if daten is not None else {}
    req = urllib.request.Request(BASIS + pfad, data=rumpf, headers=kopf)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def fahrzeug(c: dict) -> str:
    """Control Pilot und Ladezustand — der ehrlichste Teil der Auskunft."""
    cp = (c.get("cp") or "").replace("controlpi_", "").replace("v_pwm", " V+PWM")
    zustand = (c.get("state") or "?")
    return f"{cp or '?':<9} {zustand}"


def sperren(c: dict) -> str:
    teile = []
    for name, schluessel in (("Takt", "sperre_s"), ("Anlauf", "anlauf_s"),
                             ("Stromwechsel", "stromwechsel_s"),
                             ("ohne Schuetz", "nicht_geschaltet_s")):
        wert = c.get(schluessel)
        if wert:
            teile.append(f"{name} {wert}s")
    return " · ".join(teile) or "—"


def zeile(c: dict) -> str:
    return (f"{c.get('mode',''):<7} "
            f"{'will laden' if c.get('charging') else 'will nicht':<11} "
            f"{'SCHUETZ EIN' if c.get('switch_on') else 'Schuetz aus':<12} "
            f"{(c.get('power_w') or 0):>6.0f} W "
            f"{(c.get('amp') or 0):>4} A  {fahrzeug(c):<22} {sperren(c)}")


def kennzeichen(c: dict) -> tuple:
    """Woran eine Aenderung erkannt wird — Sperren zaehlen dabei nicht mit,
    die laufen ja ohnehin jede Sekunde herunter."""
    return (c.get("mode"), c.get("charging"), c.get("switch_on"),
            round((c.get("power_w") or 0) / 50.0), c.get("amp"),
            c.get("cp"), c.get("state"), tuple(c.get("faults") or []))


def main(argv=None):
    global BASIS
    p = argparse.ArgumentParser(description="Schaltvorgaenge mitschreiben")
    p.add_argument("--sekunden", type=int, default=0, help="0 = bis Strg+C")
    p.add_argument("--start", action="store_true", help="vorher auf Sofort stellen")
    p.add_argument("--stop", action="store_true", help="vorher auf Stop stellen")
    p.add_argument("--ampere", type=int, default=0, help="mit --start: Ladestrom")
    p.add_argument("--cp", default="", help="Ladepunkt (Vorgabe: der erste)")
    p.add_argument("--basis", default=BASIS)
    a = p.parse_args(argv)

    BASIS = a.basis.rstrip("/")
    # Auf den Dienst warten statt sofort aufzugeben: Nach `systemctl start`
    # dauert es ein paar Sekunden, bis der Port offen ist — und wer beide
    # Befehle zusammen abschickt, bekam bisher nur "Connection refused".
    live, letzter = None, None
    for versuch in range(20):
        try:
            live = hole("/api/live")
            break
        except (urllib.error.URLError, OSError) as e:
            letzter = e
            if versuch == 0:
                print(f"Warte auf die Steuerung auf {BASIS} …", flush=True)
            time.sleep(1)
    if live is None:
        print(f"Die Steuerung antwortet nach 20 s nicht: {letzter}")
        print("Nachsehen:  systemctl status wallbox   und   journalctl -u wallbox -n 30")
        return 1
    punkte = live.get("chargepoints") or []
    if not punkte:
        print("Kein Ladepunkt konfiguriert.")
        return 1
    cpid = a.cp or punkte[0]["id"]

    from core.paths import DATA
    datei = Path(DATA) / f"schaltlog-{time.strftime('%Y%m%d-%H%M%S')}.txt"
    mit = datei.open("w", encoding="utf-8")

    def sag(text=""):
        print(text, flush=True)
        mit.write(text + "\n")
        mit.flush()

    sag(f"Schaltlog {time.strftime('%d.%m.%Y %H:%M:%S')} · Ladepunkt {cpid}")
    sag(f"Datei: {datei}")
    sag("Hinweis: dp_dump.py --watch waehrend dieser Aufnahme NICHT laufen "
        "lassen — die Box nimmt nur eine Verbindung an.")
    sag()

    if a.start or a.stop:
        koerper = {"mode": "stop" if a.stop else "sofort"}
        if a.start and a.ampere:
            koerper["sofort_a"] = a.ampere
        try:
            hole(f"/api/chargepoint/{cpid}/mode", koerper)
            sag(f">>> von Hand: Modus {koerper['mode']}"
                + (f", {a.ampere} A" if koerper.get("sofort_a") else ""))
        except (urllib.error.URLError, OSError) as e:
            sag(f"Modus liess sich nicht setzen: {e}")

    gesehen, letztes, letzte_ausgabe = set(), None, 0.0
    ende = time.time() + a.sekunden if a.sekunden else None
    try:
        while ende is None or time.time() < ende:
            try:
                live = hole("/api/live")
            except (urllib.error.URLError, OSError) as e:
                sag(f"{time.strftime('%H:%M:%S')}  (keine Antwort: {e})")
                time.sleep(1)
                continue
            c = next((x for x in live["chargepoints"] if x["id"] == cpid), None)
            if c is None:
                sag("Ladepunkt verschwunden.")
                break

            # Schaltvorgaenge zuerst — sie erklaeren die Zeile darunter
            for e in c.get("schaltungen") or []:
                schluessel = (e["ts"], e["ein"], e["ok"])
                if schluessel in gesehen:
                    continue
                gesehen.add(schluessel)
                was = ("EIN " if e["ein"] else "AUS ") + ("" if e["ok"] else "FEHLGESCHLAGEN ")
                if e.get("selbst"):
                    was += "(DIE BOX SELBST) "
                sag(f"  >>> {time.strftime('%H:%M:%S', time.localtime(e['ts']))}  "
                    f"SCHALTUNG {was}{e.get('amp') or '?'} A  "
                    f"{e.get('work_state') or '?'} / {e.get('cp') or '?'}  "
                    f"— {e['grund']}")

            jetzt = kennzeichen(c)
            if jetzt != letztes or time.time() - letzte_ausgabe >= 30:
                sag(f"{time.strftime('%H:%M:%S')}  {zeile(c)}")
                letztes, letzte_ausgabe = jetzt, time.time()
            time.sleep(1)
    except KeyboardInterrupt:
        sag()
    sag()
    sag(f"Ende. Diese Datei verschicken:  {datei}")
    mit.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
