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
import argparse, contextlib, json, os, sys, time, urllib.error, urllib.request
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


# ── Dauerbetrieb ─────────────────────────────────────────────────────────
ORDNER = "schaltlog"
DIENST = "/etc/systemd/system/wallbox-schaltlog.service"


def _tagesdateien(tag: str):
    """Je Tag eine Textdatei zum Lesen und eine JSONL-Datei zum Auswerten.

    Beides, weil beides gebraucht wird: Der Mensch liest Zeilen, die Auswertung
    braucht Felder. Die Textdatei aus einem Programm wieder zu zerlegen waere
    unnoetig bruechig — das Format ist fuer Augen gemacht, nicht fuer Parser.
    """
    from core.paths import DATA
    ordner = Path(DATA) / ORDNER
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner / f"{tag}.txt", ordner / f"{tag}.jsonl"


def _aufraeumen(tage: int):
    from core.paths import DATA
    ordner = Path(DATA) / ORDNER
    if not ordner.is_dir() or tage <= 0:
        return
    grenze = time.time() - tage * 86400
    for f in ordner.iterdir():
        if f.is_file() and f.stat().st_mtime < grenze:
            f.unlink(missing_ok=True)


def dauerhaft(cpid: str, takt: float, behalten: int) -> int:
    """Endlos mitschreiben, je Tag eine Datei. Laeuft als Dienst.

    Kein Modus wird gesetzt und nichts geschaltet — nur zusehen. Faellt die
    Steuerung aus, wird gewartet statt aufgegeben: Ein Dauerlog, der beim
    ersten Neustart des Dienstes stirbt, ist keiner.
    """
    tag = gesehen = None
    letztes, letzte_zeile = None, 0.0
    bekannt: set = set()
    txt = js = None
    while True:
        heute = time.strftime("%Y-%m-%d")
        if heute != tag:
            for f in (txt, js):
                if f:
                    f.close()
            p_txt, p_js = _tagesdateien(heute)
            txt, js = p_txt.open("a", encoding="utf-8"), p_js.open("a", encoding="utf-8")
            txt.write(f"\n=== Mitschnitt ab {time.strftime('%d.%m.%Y %H:%M:%S')} ===\n")
            txt.flush()
            tag = heute
            letztes = None                      # nach dem Tageswechsel eine Standzeile
            _aufraeumen(behalten)
        try:
            live = hole("/api/live")
            c = next((x for x in live["chargepoints"] if x["id"] == cpid), None)
        except (urllib.error.URLError, OSError, KeyError) as e:
            if letztes != "weg":
                txt.write(f"{time.strftime('%H:%M:%S')}  (Steuerung antwortet nicht: {e})\n")
                txt.flush()
                letztes = "weg"
            time.sleep(max(takt, 5.0))
            continue
        if c is None:
            time.sleep(takt)
            continue

        for e in c.get("schaltungen") or []:
            schluessel = (e["ts"], e["ein"], e["ok"])
            if schluessel in bekannt:
                continue
            bekannt.add(schluessel)
            was = ("EIN " if e["ein"] else "AUS ") + ("" if e["ok"] else "FEHLGESCHLAGEN ")
            if e.get("selbst"):
                was += "(DIE BOX SELBST) "
            txt.write(f"  >>> {time.strftime('%H:%M:%S', time.localtime(e['ts']))}  "
                      f"SCHALTUNG {was}{e.get('amp') or '?'} A  "
                      f"{e.get('work_state') or '?'} / {e.get('cp') or '?'}"
                      f"  — {e['grund']}\n")
            js.write(json.dumps({"art": "schaltung", **e}, ensure_ascii=False) + "\n")
        if len(bekannt) > 5000:                 # das Gedaechtnis darf nicht wachsen
            bekannt = {k for k in bekannt if k[0] > time.time() - 86400}

        jetzt = kennzeichen(c)
        if jetzt != letztes or time.time() - letzte_zeile >= 300:
            txt.write(f"{time.strftime('%H:%M:%S')}  {zeile(c)}\n")
            js.write(json.dumps({"art": "zustand", "ts": time.time(),
                                 **{k: c.get(k) for k in
                                    ("mode", "charging", "switch_on", "power_w",
                                     "amp", "cp", "state", "faults", "session_kwh",
                                     "total_kwh", "sperre_s", "anlauf_s",
                                     "nicht_geschaltet_s")}},
                                ensure_ascii=False) + "\n")
            letztes, letzte_zeile = jetzt, time.time()
        txt.flush()
        js.flush()
        time.sleep(takt)


def auswerten(tage: int) -> int:
    """Die Tage zusammenfassen: Ladevorgaenge, Schaltvorgaenge, wer geschaltet hat."""
    from core.paths import DATA
    ordner = Path(DATA) / ORDNER
    dateien = sorted(ordner.glob("*.jsonl"))[-max(1, tage):] if ordner.is_dir() else []
    if not dateien:
        print(f"Keine Aufzeichnung in {ordner}.")
        print("Laeuft der Dienst?  systemctl status wallbox-schaltlog")
        return 1
    zeilen = []
    for f in dateien:
        for z in f.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(Exception):
                zeilen.append(json.loads(z))
    zeilen.sort(key=lambda e: e.get("ts", 0))
    print(f"── {len(dateien)} Tag(e), {len(zeilen)} Eintraege "
          f"({dateien[0].stem} bis {dateien[-1].stem}) ──\n")

    # Ladevorgaenge: beginnt mit Leistung, endet, wenn sie wegbleibt
    vorgaenge, offen = [], None
    for e in zeilen:
        if e.get("art") != "zustand":
            continue
        watt = e.get("power_w") or 0
        if watt > 200 and offen is None:
            offen = {"von": e["ts"], "bis": e["ts"], "max": watt,
                     "amp": e.get("amp"), "kwh": e.get("session_kwh") or 0}
        elif watt > 200:
            offen["bis"] = e["ts"]
            offen["max"] = max(offen["max"], watt)
            offen["kwh"] = max(offen["kwh"], e.get("session_kwh") or 0)
        elif offen is not None and e["ts"] - offen["bis"] > 120:
            vorgaenge.append(offen)
            offen = None
    if offen is not None:
        vorgaenge.append(offen)

    print(f"Ladevorgaenge: {len(vorgaenge)}")
    for v in vorgaenge:
        dauer = (v["bis"] - v["von"]) / 60.0
        print(f"  {time.strftime('%d.%m. %H:%M', time.localtime(v['von']))} – "
              f"{time.strftime('%H:%M', time.localtime(v['bis']))}  "
              f"{dauer:5.0f} min  bis {v['max']:5.0f} W  bei {v['amp'] or '?'} A  "
              f"{v['kwh']:.2f} kWh")

    sch = [e for e in zeilen if e.get("art") == "schaltung"]
    selbst = [e for e in sch if e.get("selbst")]
    fehl = [e for e in sch if not e.get("ok")]
    print(f"\nSchaltvorgaenge: {len(sch)}  "
          f"(davon {len(selbst)} von der Box selbst, {len(fehl)} fehlgeschlagen)")
    gruende: dict = {}
    for e in sch:
        schl = ("EIN" if e["ein"] else "AUS") + ("*" if e.get("selbst") else "") \
            + " · " + (e.get("grund") or "")[:60]
        gruende[schl] = gruende.get(schl, 0) + 1
    for schl, n in sorted(gruende.items(), key=lambda x: -x[1]):
        print(f"  {n:4d} x  {schl}")

    stoerungen = {tuple(e.get("faults") or ()) for e in zeilen
                  if e.get("art") == "zustand" and e.get("faults")}
    if stoerungen:
        print("\nGemeldete Stoerungen:")
        for st in stoerungen:
            print("  " + ", ".join(st))
    print(f"\nDie Textfassung zum Mitlesen liegt in {ordner}/*.txt")
    return 0


def dienst_einrichten(takt: float, behalten: int) -> int:
    """Den Dauerlog als systemd-Dienst einrichten und starten."""
    import subprocess
    if os.geteuid() != 0:
        print("Dafuer braucht es root:  sudo python3 tools/schaltlog.py --dienst")
        return 1
    hier = Path(__file__).resolve()
    einheit = f"""[Unit]
Description=GridMine Wallbox — Dauerhafter Schaltmitschnitt
After=wallbox.service
Wants=wallbox.service

[Service]
Type=simple
WorkingDirectory={hier.parent.parent}
Environment=GM_DATA={hier.parent.parent}/data
ExecStart=/usr/bin/python3 {hier} --dauerhaft --takt {takt} --behalten {behalten}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
    Path(DIENST).write_text(einheit, encoding="utf-8")
    for befehl in (["systemctl", "daemon-reload"],
                   ["systemctl", "enable", "--now", "wallbox-schaltlog"]):
        subprocess.run(befehl, check=False)
    print(f"Eingerichtet: {DIENST}")
    print("Laeuft jetzt und nach jedem Neustart. Nachsehen:")
    print("  systemctl status wallbox-schaltlog --no-pager | head -5")
    print("  python3 tools/schaltlog.py --auswerten")
    return 0


def main(argv=None):
    global BASIS
    p = argparse.ArgumentParser(description="Schaltvorgaenge mitschreiben")
    p.add_argument("--sekunden", type=int, default=0, help="0 = bis Strg+C")
    p.add_argument("--start", action="store_true", help="vorher auf Sofort stellen")
    p.add_argument("--stop", action="store_true", help="vorher auf Stop stellen")
    p.add_argument("--ampere", type=int, default=0, help="mit --start: Ladestrom")
    p.add_argument("--cp", default="", help="Ladepunkt (Vorgabe: der erste)")
    p.add_argument("--basis", default=BASIS)
    p.add_argument("--dauerhaft", action="store_true",
                   help="endlos mitschreiben, je Tag eine Datei (fuer den Dienst)")
    p.add_argument("--dienst", action="store_true",
                   help="den Dauerlog als systemd-Dienst einrichten und starten")
    p.add_argument("--auswerten", action="store_true",
                   help="die Aufzeichnung zusammenfassen")
    p.add_argument("--tage", type=int, default=5, help="mit --auswerten: wie viele Tage")
    p.add_argument("--takt", type=float, default=2.0, help="Sekunden zwischen den Abfragen")
    p.add_argument("--behalten", type=int, default=30,
                   help="Aufzeichnung so viele Tage aufbewahren")
    a = p.parse_args(argv)

    BASIS = a.basis.rstrip("/")
    if a.dienst:
        return dienst_einrichten(a.takt, a.behalten)
    if a.auswerten:
        return auswerten(a.tage)
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
    if a.dauerhaft:
        return dauerhaft(cpid, a.takt, a.behalten)

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
