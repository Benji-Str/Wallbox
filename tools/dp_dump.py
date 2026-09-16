#!/usr/bin/env python3
"""Alle Datenpunkte der Wallbox anzeigen — und Aenderungen sichtbar machen.

Gedacht fuer die Frage: WELCHER Datenpunkt schaltet die Kartenfreigabe?
Das Tuya-Datenmodell der OS-EC01 nennt keinen Kartenleser (nur die Stoerung
`card_reader_fault`). Welcher DP sich beim Vorhalten einer Karte bewegt,
laesst sich aber messen:

    python3 tools/dp_dump.py                    # einmal alles ausgeben
    python3 tools/dp_dump.py --watch            # dauernd, nur Aenderungen

Vorgehen, um die Kartensperre zu finden:
  1. `--watch` starten und ein paar Sekunden zusehen (Rauschen erkennen:
     Temperatur und Leistung aendern sich dauernd, das ist normal)
  2. Karte vorhalten
  3. Welcher DP genau in diesem Moment umspringt, ist der Kandidat

Es wird ausschliesslich GELESEN. Unbekannte Datenpunkte einfach zu setzen
waere an einem Geraet mit 22 kW keine gute Idee.
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.paths import DATA, venv_hinweis                     # noqa: E402

# Was wir sicher wissen — der Rest wird als „unbekannt" ausgewiesen und
# nicht geraten.
BEKANNT = {
    "1": "Zaehlerstand gesamt (0,01 kWh)",
    "3": "work_state — Ladezustand",
    "4": "charge_cur_set — eingestellter Ladestrom (A)",
    "6": "Strom L1 (0,1 A)", "7": "Strom L2 (0,1 A)", "8": "Strom L3 (0,1 A)",
    "9": "power_total — Leistung (W)",
    "10": "fault — Stoerungs-Bitmap",
    "13": "connection_state — Control Pilot",
    "14": "work_mode — Betriebsart",
    "18": "switch — Laden ein/aus (auch die KARTE schaltet hier, gemessen)",
    "23": "Firmware-Version der Box",
    "24": "temp_current — Temperatur (°C)",
    "25": "Energie des laufenden Vorgangs (0,01 kWh)",
}


def lies_cfg() -> dict:
    for p in (DATA / "wallbox.json", Path("config.example.json")):
        if p.exists():
            cfg = json.loads(p.read_text(encoding="utf-8"))
            for c in cfg.get("chargepoints", []):
                if c.get("device_id") and c.get("local_key"):
                    return c
    print("Keine Wallbox mit device_id und local_key in der Konfiguration.")
    print("Erst tools/tuya_setup.py laufen lassen.")
    sys.exit(1)


def hole(d) -> dict:
    st = d.status() or {}
    return {str(k): v for k, v in (st.get("dps") or {}).items()}


def zeige(dps: dict):
    for k in sorted(dps, key=lambda x: int(x) if x.isdigit() else 999):
        name = BEKANNT.get(k, "unbekannt — Kandidat")
        print(f"  DP {k:>3}  {str(dps[k])[:40]:<42} {name}")


def main():
    try:
        import tinytuya
    except ImportError:
        print(venv_hinweis("tinytuya"))
        sys.exit(1)
    c = lies_cfg()
    d = tinytuya.Device(c["device_id"], c["ip"], c["local_key"])
    d.set_version(float(c.get("protocol", 3.4)))
    d.set_socketTimeout(5)

    dps = hole(d)
    if not dps:
        print("Keine Antwort. Stimmen IP, device_id und local_key?")
        sys.exit(1)
    print(f"Wallbox {c['ip']} — {len(dps)} Datenpunkte:")
    zeige(dps)

    if "--watch" not in sys.argv:
        return
    print("\nBeobachte Aenderungen (Strg+C beendet). Jetzt die Karte vorhalten.")
    # Werte, die sich staendig von selbst bewegen — sonst geht die eine
    # interessante Aenderung im Rauschen unter.
    rauschen = {"9", "24", "1", "25", "6", "7", "8"}
    while True:
        time.sleep(1)
        try:
            neu = hole(d)
        except Exception as e:
            print(f"  {time.strftime('%H:%M:%S')}  (keine Antwort: {e})")
            continue
        if not neu:
            # Eine leere Antwort ist KEINE Aenderung. Ohne diese Zeile meldete
            # das Werkzeug jeden einzelnen Datenpunkt als "-> None" und eine
            # Sekunde spaeter wieder zurueck — und pries dabei die
            # Firmware-Version als Kandidaten fuer die Karte an. Ein Messgeraet,
            # das eine gestoerte Messung als Messwert ausgibt, schickt in die
            # Irre.
            print(f"  {time.strftime('%H:%M:%S')}  (leere Antwort — uebersprungen)")
            continue
        for k in sorted(set(dps) | set(neu)):
            if dps.get(k) == neu.get(k) or k in rauschen:
                continue
            print(f"  {time.strftime('%H:%M:%S')}  DP {k}: "
                  f"{dps.get(k)!r} -> {neu.get(k)!r}   "
                  f"{BEKANNT.get(k, '*** unbekannt — das koennte die Karte sein ***')}")
        dps = neu


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
