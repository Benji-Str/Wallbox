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
import argparse, contextlib, json, os, socket, sys, time
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
    # Der Zaehlerstand ist der zweite, unabhaengige Beweis. Wenn DP9 auf dieser
    # Firmware nicht mitkommt, sagt ein steigender Zaehler trotzdem, dass Strom
    # fliesst — und darauf kommt es an, nicht auf einen bestimmten Datenpunkt.
    kwh = zahl(dps.get("1"))
    cp = dps.get(str(c.get("dp_connection", 13)))
    return {
        "an": bool(dps.get(str(c.get("dp_switch", 18)))),
        "zustand": dps.get(str(c.get("dp_state", 3))),
        "cp": cp,
        "cp_text": CP_TEXT.get(cp, cp or "?"),
        "amp": amp, "watt": watt,
        "kwh": None if kwh is None else kwh / 100.0,
        "fehlt": [n for n, dp in (("Leistung", c.get("dp_power", 9)),
                                  ("Ladezustand", c.get("dp_state", 3)),
                                  ("Control Pilot", c.get("dp_connection", 13)))
                  if str(dp) not in dps],
        "modus": dps.get(str(c.get("dp_mode", 14))),
        "stoerungen": _stoerungen(dps.get(str(c.get("dp_fault", 10)))),
    }


def kennzeichen(b: dict) -> tuple:
    """Leistung in 100-W-Stufen: sonst steht bei jedem Messrauschen eine Zeile."""
    return (b["an"], b["zustand"], b["cp"], b["amp"], b["modus"],
            None if b["watt"] is None else round(b["watt"] / 100.0),
            None if b.get("kwh") is None else round(b["kwh"], 2),
            tuple(b["stoerungen"]))


def zeile(b: dict) -> str:
    return (f"{'SCHUETZ EIN' if b['an'] else 'Schuetz aus':<12} "
            f"{(b['watt'] or 0):>6.0f} W {(b['amp'] or 0):>4.0f} A  "
            f"{str(b['zustand'] or '?'):<18} {b['cp_text']}"
            + (f"  Zaehler {b['kwh']:.2f} kWh" if b.get("kwh") else "")
            + ("  OHNE: " + ", ".join(b.get("fehlt") or []) if b.get("fehlt") else "")
            + ("  STOERUNG: " + ", ".join(b["stoerungen"]) if b["stoerungen"] else ""))


# ── Dauerbetrieb ohne Steuerung ──────────────────────────────────────────
ORDNER = "ladelog"
DIENST = "/etc/systemd/system/wallbox-ladelog.service"


def _tagesdateien(tag: str):
    """Je Tag eine Textdatei zum Lesen und eine JSONL-Datei zum Auswerten."""
    ordner = Path(DATA) / ORDNER
    ordner.mkdir(parents=True, exist_ok=True)
    return ordner / f"{tag}.txt", ordner / f"{tag}.jsonl"


def _aufraeumen(tage: int):
    ordner = Path(DATA) / ORDNER
    if not ordner.is_dir() or tage <= 0:
        return
    grenze = time.time() - tage * 86400
    for f in ordner.iterdir():
        if f.is_file() and f.stat().st_mtime < grenze:
            f.unlink(missing_ok=True)


def dauerhaft(c: dict, takt: float, behalten: int) -> int:
    """Endlos an der Box mitlesen, je Tag eine Datei. Laeuft als Dienst.

    Gedacht fuer Tage, an denen die Steuerung AUS ist und von Hand — mit der
    Karte — geladen wird. Dann ist die Box frei, und wir sehen ungestoert, was
    sie und das Fahrzeug miteinander ausmachen.

    Geschrieben wird nichts. Eine gestoerte Abfrage beendet den Mitschnitt
    nicht: Die Verbindung wird neu aufgebaut und weitergemessen.
    """
    import tinytuya
    d = tinytuya.Device(c["device_id"], c["ip"], c["local_key"])
    d.set_version(float(c.get("protocol", 3.4)))
    d.set_socketTimeout(5)
    d.set_socketPersistent(True)

    tag, txt, js = None, None, None
    letztes, letzte_zeile, stille = None, 0.0, 0
    while True:
        heute = time.strftime("%Y-%m-%d")
        if heute != tag:
            for f in (txt, js):
                if f:
                    f.close()
            p_txt, p_js = _tagesdateien(heute)
            txt, js = p_txt.open("a", encoding="utf-8"), p_js.open("a", encoding="utf-8")
            txt.write(f"\n=== Mitschnitt ab {time.strftime('%d.%m.%Y %H:%M:%S')} "
                      f"(Steuerung aus, Bedienung ueber Karte) ===\n")
            txt.flush()
            tag, letztes = heute, None
            _aufraeumen(behalten)
        try:
            st = d.status() or {}
        except Exception as e:
            if stille == 0:
                txt.write(f"{time.strftime('%H:%M:%S')}  (Abfrage fehlgeschlagen: {e})\n")
                txt.flush()
            stille += 1
            d.set_socketPersistent(False)
            time.sleep(max(takt, 5.0))
            d.set_socketPersistent(True)
            continue
        dps = {str(k): v for k, v in (st.get("dps") or {}).items()}
        if not dps:
            time.sleep(takt)
            continue
        stille = 0
        b = bild(dps, c)
        jetzt = kennzeichen(b)
        if jetzt != letztes or time.time() - letzte_zeile >= 300:
            txt.write(f"{time.strftime('%H:%M:%S')}  {zeile(b)}\n")
            js.write(json.dumps({"ts": time.time(), "an": b["an"],
                                 "zustand": b["zustand"], "cp": b["cp"],
                                 "amp": b["amp"], "watt": b["watt"],
                                 "kwh": b.get("kwh"), "modus": b["modus"],
                                 "stoerungen": b["stoerungen"]},
                                ensure_ascii=False) + "\n")
            txt.flush()
            js.flush()
            letztes, letzte_zeile = jetzt, time.time()
        time.sleep(takt)


def auswerten(tage: int) -> int:
    """Die Tage zusammenfassen: Ladevorgaenge, Schaltvorgaenge der Box, Wege."""
    ordner = Path(DATA) / ORDNER
    dateien = sorted(ordner.glob("*.jsonl"))[-max(1, tage):] if ordner.is_dir() else []
    if not dateien:
        print(f"Keine Aufzeichnung in {ordner}.")
        print("Laeuft der Dienst?  systemctl status wallbox-ladelog")
        return 1
    z = []
    for f in dateien:
        for r in f.read_text(encoding="utf-8").splitlines():
            with contextlib.suppress(Exception):
                z.append(json.loads(r))
    z.sort(key=lambda e: e.get("ts", 0))
    print(f"── {len(dateien)} Tag(e), {len(z)} Eintraege "
          f"({dateien[0].stem} bis {dateien[-1].stem}) · Steuerung war aus ──\n")

    # Ladevorgaenge
    vorgaenge, offen = [], None
    for e in z:
        watt = e.get("watt") or 0
        if watt > 200 and offen is None:
            offen = {"von": e["ts"], "bis": e["ts"], "max": watt, "amp": e.get("amp"),
                     "kwh_von": e.get("kwh"), "kwh_bis": e.get("kwh")}
        elif watt > 200:
            offen.update(bis=e["ts"], max=max(offen["max"], watt),
                         amp=e.get("amp") or offen["amp"], kwh_bis=e.get("kwh"))
        elif offen is not None and e["ts"] - offen["bis"] > 180:
            vorgaenge.append(offen)
            offen = None
    if offen is not None:
        vorgaenge.append(offen)

    print(f"Ladevorgaenge: {len(vorgaenge)}")
    gesamt = 0.0
    for v in vorgaenge:
        kwh = ((v["kwh_bis"] - v["kwh_von"])
               if v["kwh_von"] is not None and v["kwh_bis"] is not None else None)
        gesamt += kwh or 0.0
        print(f"  {time.strftime('%a %d.%m. %H:%M', time.localtime(v['von']))} – "
              f"{time.strftime('%H:%M', time.localtime(v['bis']))}  "
              f"{(v['bis'] - v['von']) / 60:5.0f} min  bis {v['max']:5.0f} W  "
              f"bei {v['amp'] or '?'} A"
              + (f"  {kwh:5.2f} kWh" if kwh is not None else ""))
    if gesamt:
        print(f"  zusammen {gesamt:.2f} kWh")

    # Wer schaltet den Schuetz — hier kann es nur die Box oder die Karte sein
    wechsel = [(z[i]["ts"], z[i]["an"]) for i in range(1, len(z))
               if z[i]["an"] != z[i - 1]["an"]]
    ein = sum(1 for _, an in wechsel if an)
    print(f"\nSchuetz-Wechsel: {len(wechsel)}  ({ein} x ein, {len(wechsel) - ein} x aus)")
    print("  Die Steuerung war aus — es war also die Karte oder die Box selbst.")

    # Vom Anstecken bis zum Laden
    wege = []
    steckte = None
    for e in z:
        frei = "free" in str(e.get("zustand") or "").lower()
        if frei:
            steckte = None
        elif steckte is None:
            steckte = e["ts"]
        elif (e.get("watt") or 0) > 200 and steckte:
            wege.append(e["ts"] - steckte)
            steckte = None
    if wege:
        print(f"\nVom Anstecken bis zum Laden: {len(wege)} mal, "
              f"im Mittel {sum(wege) / len(wege):.0f} s "
              f"(schnellstens {min(wege):.0f} s, laengstens {max(wege):.0f} s)")

    stoer = {tuple(e.get("stoerungen") or ()) for e in z if e.get("stoerungen")}
    if stoer:
        print("\nGemeldete Stoerungen:")
        for s_ in stoer:
            print("  " + ", ".join(s_))
    print(f"\nDie Textfassung zum Mitlesen liegt in {ordner}/*.txt")
    return 0


def dienst_einrichten(takt: float, behalten: int) -> int:
    """Den Beobachtungs-Dienst einrichten. Er schliesst die Steuerung aus."""
    import subprocess
    if os.geteuid() != 0:
        print("Dafuer braucht es root:  sudo python3 tools/ladelog.py --dienst")
        return 1
    hier = Path(__file__).resolve()
    wurzel = hier.parent.parent
    python = wurzel / ".venv" / "bin" / "python3"
    einheit = f"""[Unit]
Description=GridMine Wallbox — Beobachtung ohne Steuerung (Kartenbetrieb)
# Die Box nimmt nur EINE Verbindung an. Conflicts sorgt dafuer, dass beim
# Start dieses Dienstes die Steuerung angehalten wird — und umgekehrt. Ohne
# das misst man den Streit um die Verbindung statt der Anlage.
Conflicts=wallbox.service

[Service]
Type=simple
WorkingDirectory={wurzel}
Environment=GM_DATA={wurzel}/data
ExecStart={python} {hier} --dauerhaft --takt {takt} --behalten {behalten}
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
"""
    Path(DIENST).write_text(einheit, encoding="utf-8")
    for befehl in (["systemctl", "daemon-reload"],
                   ["systemctl", "disable", "--now", "wallbox"],
                   ["systemctl", "enable", "--now", "wallbox-ladelog"]):
        subprocess.run(befehl, check=False)
    print(f"Eingerichtet: {DIENST}")
    print("Die Steuerung ist damit ANGEHALTEN und startet nicht mehr von selbst —")
    print("sieben Tage Kartenbetrieb, ungestoert.")
    print()
    print("Nachsehen:   systemctl status wallbox-ladelog --no-pager | head -5")
    print("Auswerten:   python3 tools/ladelog.py --auswerten --tage 7")
    print("Zurueck zur Steuerung:")
    print("  systemctl disable --now wallbox-ladelog && systemctl enable --now wallbox")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(description="Eine Ladung ohne Steuerung mitschreiben")
    p.add_argument("--minuten", type=float, default=0, help="0 = bis Strg+C")
    p.add_argument("--takt", type=float, default=2.0, help="Sekunden zwischen den Abfragen")
    p.add_argument("--trotzdem", action="store_true",
                   help="auch bei laufendem Dienst (nicht empfohlen)")
    p.add_argument("--dauerhaft", action="store_true",
                   help="endlos mitschreiben, je Tag eine Datei (fuer den Dienst)")
    p.add_argument("--dienst", action="store_true",
                   help="Beobachtung als Dienst einrichten, Steuerung anhalten")
    p.add_argument("--auswerten", action="store_true", help="die Tage zusammenfassen")
    p.add_argument("--tage", type=int, default=7, help="mit --auswerten: wie viele Tage")
    p.add_argument("--behalten", type=int, default=30,
                   help="Aufzeichnung so viele Tage aufbewahren")
    a = p.parse_args(argv)

    if a.dienst:
        return dienst_einrichten(a.takt, a.behalten)
    if a.auswerten:
        return auswerten(a.tage)

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
    if a.dauerhaft:
        return dauerhaft(c, a.takt, a.behalten)
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
    max_watt, lud_s = 0.0, 0.0
    kwh_anfang = kwh_ende = None
    abfragen = gelungen = 0
    ende = time.time() + a.minuten * 60 if a.minuten else None
    try:
        while ende is None or time.time() < ende:
            abfragen += 1
            try:
                st = d.status() or {}
            except Exception as e:
                # Ohne dieses Auffangen beendete eine einzige gestoerte Abfrage
                # den ganzen Mitschnitt — mitten in der Ladung, mit Rueckverfolgung
                # statt mit Messwerten.
                sag(f"{time.strftime('%H:%M:%S')}  (Abfrage fehlgeschlagen: {e})")
                d.set_socketPersistent(False)
                time.sleep(a.takt)
                d.set_socketPersistent(True)
                continue
            dps = {str(k): v for k, v in (st.get("dps") or {}).items()}
            if not dps:
                sag(f"{time.strftime('%H:%M:%S')}  (keine Antwort — "
                    f"{st.get('Error', 'leere Antwort')})")
                time.sleep(a.takt)
                continue
            gelungen += 1
            b = bild(dps, c)
            if gelungen == 1:
                sag(f"Gelesene Datenpunkte: {sorted(dps, key=lambda x: int(x) if x.isdigit() else 99)}")
                if b["fehlt"]:
                    sag(f"ACHTUNG: diese Box meldet nicht: {', '.join(b['fehlt'])}")
                sag()
            if b["kwh"] is not None:
                kwh_anfang = kwh_anfang if kwh_anfang is not None else b["kwh"]
                kwh_ende = b["kwh"]
            watt = b["watt"] or 0.0
            max_watt = max(max_watt, watt)
            if watt > 200:
                lud_s += a.takt
            jetzt = kennzeichen(b)
            if jetzt != letztes or time.time() - letzte_zeile >= 30 or gelungen <= 3:
                sag(f"{time.strftime('%H:%M:%S')}  {zeile(b)}")
                letztes, letzte_zeile = jetzt, time.time()
            time.sleep(a.takt)
    except KeyboardInterrupt:
        sag()

    sag()
    sag("── Ergebnis ──")
    sag(f"  {gelungen} von {abfragen} Abfragen haben geantwortet.")
    geladen_kwh = (None if kwh_anfang is None or kwh_ende is None
                   else kwh_ende - kwh_anfang)
    if geladen_kwh is not None:
        sag(f"  Zaehler der Box: {kwh_anfang:.2f} -> {kwh_ende:.2f} kWh "
            f"({geladen_kwh:+.2f})")
    hat_geladen = max_watt > 200 or (geladen_kwh or 0) > 0.02
    if hat_geladen:
        sag(f"  Das Auto HAT geladen: bis {max_watt:.0f} W, "
            f"rund {lud_s / 60:.0f} min lang.")
        sag("  Damit liegt es NICHT an der Box und nicht am Auto, sondern an "
            "der Steuerung.")
    elif gelungen == 0:
        sag("  Die Box hat auf keine einzige Abfrage geantwortet.")
        sag("  Zu pruefen: IP, device_id und local_key, und ob der Dienst "
            "wirklich gestoppt ist (er haelt sonst die einzige Verbindung).")
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
