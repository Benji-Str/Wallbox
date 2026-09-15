#!/usr/bin/env python3
"""Einrichtung in einem Rutsch: local_key holen -> Wallbox finden -> DPs lesen
-> fertigen config.json-Block ausgeben.

MUSS AUF DEM PC IM KUNDENNETZ LAUFEN (gleiches WLAN/LAN wie die Wallbox).

Vorher einmalig auf iot.tuya.com: Cloud-Projekt anlegen, SmartLife-Konto per
QR verknuepfen, dann unter "Overview" Access ID + Access Secret abholen.
Dieses Skript ersetzt danach den tinytuya-Wizard und alle Handgriffe danach.

    python3 tools/tuya_setup.py
    python3 tools/tuya_setup.py --ip 192.168.1.8 --region eu

Das Access Secret wird nur fuer diesen Aufruf verwendet und nirgends
gespeichert. Der local_key landet im ausgegebenen Config-Block — der gehoert
in die config.json und NICHT ins Git (steht schon in .gitignore).
"""
from __future__ import annotations
import argparse, getpass, json, sys

try:
    import tinytuya
except ImportError:
    sys.exit("Bitte zuerst installieren:  pip install tinytuya")

REGIONS = ("eu", "us", "cn", "in", "eu-w", "us-e", "sg")


def ask(prompt, default=""):
    v = input(f"{prompt}{f' [{default}]' if default else ''}: ").strip()
    return v or default


# ---------------------------------------------------------------- Cloud-Abruf
def fetch_devices(region, key, secret):
    """Geraeteliste inkl. local_key aus der Tuya-Cloud holen (einmalig)."""
    print("\nFrage Geraeteliste ab ...")
    cloud = tinytuya.Cloud(apiRegion=region, apiKey=key, apiSecret=secret)
    if getattr(cloud, "error", None):
        sys.exit(f"Cloud-Fehler: {cloud.error}\n"
                 "Pruefen: Access ID/Secret richtig? Region passend zum "
                 "App-Konto (AT/DE = eu)? SmartLife-Konto im Projekt verknuepft?")
    devs = cloud.getdevices()
    if isinstance(devs, dict) and "Error" in devs:
        sys.exit(f"Cloud-Fehler: {devs.get('Error')} {devs.get('Payload','')}")
    if not devs:
        sys.exit("Keine Geraete im Projekt. Ist das SmartLife-Konto unter "
                 "Devices -> Link Tuya App Account verknuepft?")
    return devs


def scan_lan():
    """Geraete im LAN suchen — liefert IP und Protokollversion."""
    print("Suche Geraete im LAN (ca. 20 s) ...")
    try:
        return tinytuya.deviceScan(False, 20) or {}
    except Exception as e:
        print(f"  LAN-Scan fehlgeschlagen ({e}) — mache ohne weiter.")
        return {}


def merge(devs, found):
    """Cloud-Liste (hat den Key) mit LAN-Scan (hat IP + Version) zusammenfuehren."""
    by_id = {}
    for ip, info in found.items():
        gw = info.get("gwId") or info.get("id")
        if gw:
            by_id[gw] = {"ip": ip, "version": str(info.get("version") or "3.3")}
    out = []
    for d in devs:
        did = d.get("id")
        lan = by_id.get(did, {})
        out.append({
            "name": d.get("name", "?"),
            "id": did,
            "key": d.get("key", ""),
            "ip": lan.get("ip") or d.get("ip") or "",
            "version": lan.get("version") or str(d.get("version") or "3.3"),
            "product": d.get("product_name", ""),
        })
    return out


# ------------------------------------------------------------ DP-Analyse
def read_dps(dev):
    d = tinytuya.Device(dev["id"], dev["ip"], dev["key"], version=float(dev["version"]))
    d.set_socketTimeout(5)
    res = d.status() or {}
    if "dps" not in res:
        return None, res.get("Error", "keine Antwort")
    return res["dps"], None


def guess_dps(dps: dict) -> dict:
    """Plausible Rollen vorschlagen. MUSS mit 'watch' geprueft werden —
    die DP-Nummern sind je Wallbox-Charge unterschiedlich."""
    g = {"dp_switch": None, "dp_current": None, "dp_power": None}
    nums = sorted(dps, key=lambda x: int(x) if str(x).isdigit() else 9999)
    for k in nums:
        v = dps[k]
        if isinstance(v, bool) and g["dp_switch"] is None:
            g["dp_switch"] = int(k)
        elif isinstance(v, (int, float)) and not isinstance(v, bool):
            if 1 <= v <= 32 and g["dp_current"] is None:
                g["dp_current"] = int(k)
            elif v > 32 and g["dp_power"] is None:
                g["dp_power"] = int(k)
    return g


def config_block(dev, g, phases):
    c = {"type": "wallbox_tuya", "ip": dev["ip"], "name": dev["name"] or "Wallbox",
         "device_id": dev["id"], "local_key": dev["key"], "protocol": dev["version"],
         "phases": phases, "volt": 230, "min_a": 6, "max_a": 32,
         "dp_switch": g["dp_switch"] if g["dp_switch"] is not None else 1,
         "dp_current": g["dp_current"] if g["dp_current"] is not None else 4,
         "min_switch_interval_s": 300}
    if g["dp_power"] is not None:
        c["dp_power"] = g["dp_power"]
    return c


# ---------------------------------------------------------------- Ablauf
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ip", default="", help="IP der Wallbox (sonst Auswahl aus Liste)")
    ap.add_argument("--region", default="eu", help=f"Rechenzentrum {REGIONS}")
    ap.add_argument("--key", default="", help="Access ID (sonst Abfrage)")
    ap.add_argument("--phases", type=int, default=3, help="1 oder 3 (Standard 3)")
    ap.add_argument("--out", default="wallbox_config.json", help="Zieldatei fuer den Block")
    a = ap.parse_args()

    print(__doc__.split("Vorher einmalig")[0])
    api_key = a.key or ask("Access ID / Client ID")
    api_secret = getpass.getpass("Access Secret (Eingabe bleibt unsichtbar): ").strip()
    region = a.region or ask(f"Region {REGIONS}", "eu")
    if not api_key or not api_secret:
        sys.exit("Access ID und Secret werden gebraucht.")

    devs = merge(fetch_devices(region, api_key, api_secret), scan_lan())

    print(f"\n{len(devs)} Geraet(e) gefunden:")
    for i, d in enumerate(devs, 1):
        print(f"  [{i}] {d['name'][:28]:28s} ip={d['ip'] or '-':15s} "
              f"v{d['version']:4s} {d['product'][:20]}")

    dev = None
    if a.ip:
        dev = next((d for d in devs if d["ip"] == a.ip), None)
        if not dev:
            print(f"\n{a.ip} war nicht dabei — bitte aus der Liste waehlen.")
    if not dev:
        n = ask("\nWelche Nummer ist die Wallbox?", "1")
        try:
            dev = devs[int(n) - 1]
        except (ValueError, IndexError):
            sys.exit("Ungueltige Auswahl.")

    if not dev["ip"]:
        dev["ip"] = ask("IP der Wallbox (LAN-Scan hat keine geliefert)", a.ip or "192.168.1.8")

    print(f"\nGewaehlt: {dev['name']} ({dev['ip']}, Protokoll {dev['version']})")
    print("Lese Datenpunkte lokal ...")

    dps, err = read_dps(dev)
    if err:
        for v in ("3.3", "3.4", "3.5"):       # Version durchprobieren
            if v == dev["version"]:
                continue
            dev["version"] = v
            dps, err = read_dps(dev)
            if not err:
                print(f"  -> Protokollversion {v} funktioniert.")
                break
    if err:
        sys.exit(f"Keine lokale Antwort ({err}).\n"
                 "Pruefen: PC im selben Netz? Wallbox per WLAN verbunden? "
                 "Firewall/Client-Isolation im Router aus?")

    print("\nAktuelle Datenpunkte:")
    for k in sorted(dps, key=lambda x: int(x) if str(x).isdigit() else 999):
        print(f"  DP {k:>4} = {dps[k]!r}")

    g = guess_dps(dps)
    cfg = config_block(dev, g, a.phases)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)

    shown = dict(cfg); shown["local_key"] = cfg["local_key"][:4] + "..." + cfg["local_key"][-2:]
    print("\nVorschlag fuer config.json -> miners[] (local_key hier gekuerzt):")
    print(json.dumps(shown, indent=2, ensure_ascii=False))
    print(f"\nVollstaendig gespeichert in: {a.out}   (enthaelt den local_key — nicht ins Git!)")

    print("\nDie DP-Zuordnung ist geraten und MUSS geprueft werden:")
    print(f"  python3 tools/tuya_scan.py watch {dev['ip']} {dev['id']} <local_key> {dev['version']}")
    print("  -> in der App Ladestrom verstellen und Laden starten;")
    print("     die DP-Nummer, die sich mitbewegt, ist dp_current bzw. dp_switch.")


if __name__ == "__main__":
    main()
