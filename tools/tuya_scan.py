#!/usr/bin/env python3
"""Wallbox im eigenen Netz finden und die Datenpunkte (DPs) herausfinden.

MUSS IM KUNDENNETZ laufen (gleiches LAN wie die Wallbox), nicht auf dem Server.

  1) Geraete suchen:        python3 tools/tuya_scan.py scan
  2) Status einmal lesen:   python3 tools/tuya_scan.py status <ip> <device_id> <local_key> [version]
  3) DPs zuordnen (live):   python3 tools/tuya_scan.py watch  <ip> <device_id> <local_key> [version]
     -> in der SmartLife-App Strom verstellen / Laden starten und zusehen,
        welche DP-Nummer sich aendert. Genau die kommt in die config.json.

local_key einmalig besorgen (danach nie wieder Cloud):
  pip install tinytuya && python3 -m tinytuya wizard
  (kostenloser Tuya-IoT-Developer-Account, App-Konto verknuepfen, Key notieren —
   anschliessend darf die Wallbox im Router komplett vom Internet getrennt werden)
"""
import sys, time
from pathlib import Path as _P; sys.path.insert(0, str(_P(__file__).resolve().parent.parent))
from core.paths import venv_hinweis                            # noqa: E402

try:
    import tinytuya
except ImportError:
    sys.exit(venv_hinweis("tinytuya"))


def _dev(ip, did, key, ver):
    d = tinytuya.Device(did, ip, key, version=float(ver))
    d.set_socketTimeout(5)
    d.set_socketPersistent(True)
    return d


def cmd_scan():
    print("Suche Tuya-Geraete im LAN (ca. 18 s) ...")
    found = tinytuya.deviceScan(False, 20)
    if not found:
        print("Nichts gefunden. Pruefen: gleiches Subnetz? UDP 6666/6667 erlaubt?")
        return
    for ip, info in found.items():
        print(f"  {ip:16s} id={info.get('gwId','?')} version={info.get('version','?')} "
              f"product={info.get('productKey','?')}")


def cmd_status(ip, did, key, ver="3.4"):
    dps = (_dev(ip, did, key, ver).status() or {}).get("dps", {})
    if not dps:
        sys.exit("Keine Antwort — Version (3.3/3.4/3.5) oder local_key falsch?")
    for k in sorted(dps, key=lambda x: int(x) if str(x).isdigit() else 999):
        print(f"  DP {k:>4} = {dps[k]!r}")


def cmd_watch(ip, did, key, ver="3.4"):
    d = _dev(ip, did, key, ver)
    print("Beobachte DPs — jetzt in der App Ladestrom aendern / Laden starten.")
    print("Abbruch mit Strg+C\n")
    last = {}
    while True:
        try:
            dps = (d.status() or {}).get("dps", {}) or {}
            for k, v in dps.items():
                if last.get(k) != v:
                    mark = "NEU " if k not in last else "AEND"
                    print(f"[{time.strftime('%H:%M:%S')}] {mark} DP {k:>4} : {last.get(k)!r} -> {v!r}")
            last = dict(dps)
        except KeyboardInterrupt:
            return
        except Exception as e:
            print("Fehler:", e)
        time.sleep(2)


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] == "scan":
        cmd_scan()
    elif a[0] == "status" and len(a) >= 4:
        cmd_status(*a[1:5])
    elif a[0] == "watch" and len(a) >= 4:
        cmd_watch(*a[1:5])
    else:
        print(__doc__)
