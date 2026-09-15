#!/usr/bin/env python3
"""Wallbox abklopfen: was bietet die Box im eigenen Netz ueberhaupt an?

Braucht NICHTS ausser Python — keine Installation, kein Tuya-Konto.
Muss auf einem PC im selben Netz wie die Wallbox laufen.

    python3 tools/wallbox_probe.py 192.168.1.8

Prueft der Reihe nach:
  1. Welche Ports offen sind (Web-Oberflaeche? Modbus? OCPP? Tuya?)
  2. Ob eine Web-Oberflaeche antwortet (dann ginge es ganz ohne Tuya)
  3. Ob Modbus/TCP antwortet (dann ginge es sauber ueber Register)
  4. Tuya-Suchmeldungen im Netz (liefert device_id + Protokollversion,
     ganz ohne Cloud — nur der local_key fehlt dann noch)
"""
from __future__ import annotations
import socket, struct, sys, json

PORTS = {
    80:   "HTTP (Web-Oberflaeche)",
    443:  "HTTPS",
    502:  "Modbus/TCP",
    1883: "MQTT",
    4028: "cgminer-API",
    6668: "Tuya lokal",
    8080: "HTTP alternativ / OCPP",
    8081: "HTTP alternativ",
    8443: "HTTPS alternativ",
    8888: "HTTP alternativ",
    9999: "Hersteller-Dienst",
}


def check_port(ip, port, timeout=1.5):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        return s.connect_ex((ip, port)) == 0
    except Exception:
        return False
    finally:
        s.close()


def http_probe(ip, port):
    """Rohes HTTP GET — ohne Bibliotheken, damit nichts installiert sein muss."""
    scheme_note = ""
    try:
        s = socket.create_connection((ip, port), timeout=4)
        if port in (443, 8443):
            try:
                import ssl
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                s = ctx.wrap_socket(s)
                scheme_note = " (TLS)"
            except Exception as e:
                return f"TLS fehlgeschlagen: {e}"
        s.sendall(f"GET / HTTP/1.1\r\nHost: {ip}\r\nConnection: close\r\n\r\n".encode())
        data = b""
        while len(data) < 4096:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
        s.close()
        text = data.decode("utf-8", "replace")
        head = text.split("\r\n\r\n", 1)[0]
        status = head.split("\r\n")[0] if head else "?"
        server = next((l for l in head.split("\r\n") if l.lower().startswith("server:")), "")
        title = ""
        low = text.lower()
        if "<title>" in low:
            i = low.index("<title>") + 7
            title = text[i:low.index("</title>", i)].strip()[:60]
        return f"{status}{scheme_note}  {server}  {('Titel: ' + title) if title else ''}".strip()
    except Exception as e:
        return f"keine HTTP-Antwort ({type(e).__name__})"


def modbus_probe(ip, port=502):
    """Read Holding Registers (FC3), Register 0, 1 Stueck — Standard-Anfrage."""
    req = struct.pack(">HHHBBHH", 1, 0, 6, 1, 3, 0, 1)
    try:
        s = socket.create_connection((ip, port), timeout=4)
        s.sendall(req)
        r = s.recv(256)
        s.close()
        if len(r) >= 8 and r[7] in (3, 0x83):
            return ("antwortet (Fehlercode — aber Modbus spricht sie)" if r[7] == 0x83
                    else f"ANTWORTET, Register 0 = {struct.unpack('>H', r[9:11])[0]}")
        return f"unklare Antwort: {r[:20].hex()}"
    except Exception as e:
        return f"keine Modbus-Antwort ({type(e).__name__})"


def tuya_discover(seconds=12):
    """Tuya-Geraete senden ihre Kennung per UDP-Broadcast — ohne Cloud lesbar."""
    out = {}
    try:
        import tinytuya
        found = tinytuya.deviceScan(False, seconds) or {}
        for ip, info in found.items():
            out[ip] = {"id": info.get("gwId") or info.get("id"),
                       "version": str(info.get("version") or "?"),
                       "product": info.get("productKey", "")}
        return out, None
    except ImportError:
        pass
    except Exception as e:
        return {}, f"tinytuya-Scan fehlgeschlagen: {e}"

    # Notfalls ohne tinytuya: Port 6666 sendet unverschluesselt
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(seconds)
        s.bind(("", 6666))
        data, addr = s.recvfrom(2048)
        s.close()
        body = data[20:-8]
        j = json.loads(body.decode("utf-8", "replace"))
        return {addr[0]: {"id": j.get("gwId"), "version": str(j.get("version", "?")),
                          "product": j.get("productKey", "")}}, None
    except Exception as e:
        return {}, f"keine unverschluesselte Suchmeldung ({type(e).__name__})"


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else "192.168.1.8"
    print(f"\nPruefe Wallbox {ip}\n" + "=" * 58)

    print("\n[1] Offene Ports")
    open_ports = []
    for p, label in PORTS.items():
        if check_port(ip, p):
            open_ports.append(p)
            print(f"    OFFEN   {p:>5}  {label}")
    if not open_ports:
        print("    keiner der geprueften Ports ist offen.")
        print("    -> Ist die IP richtig? Ist die Box im WLAN? Client-Isolation im Router aus?")

    web = [p for p in open_ports if p in (80, 443, 8080, 8081, 8443, 8888)]
    if web:
        print("\n[2] Web-Oberflaeche")
        for p in web:
            print(f"    Port {p}: {http_probe(ip, p)}")

    if 502 in open_ports:
        print("\n[3] Modbus/TCP")
        print(f"    {modbus_probe(ip)}")

    print("\n[4] Tuya-Suchmeldungen im Netz (ohne Cloud)")
    devs, err = tuya_discover()
    if devs:
        for dip, d in devs.items():
            mark = "  <-- deine Wallbox" if dip == ip else ""
            print(f"    {dip:16s} id={d['id']} protokoll={d['version']}{mark}")
    else:
        print(f"    nichts empfangen. {err or ''}")

    print("\n" + "=" * 58)
    print("ERGEBNIS")
    if web:
        print("  Es gibt eine Web-Oberflaeche -> im Browser oeffnen: "
              f"http://{ip}{'' if 80 in web else ':' + str(web[0])}")
        print("  Wenn dort Einstellungen/OCPP auftauchen, brauchen wir Tuya gar nicht.")
    if 502 in open_ports:
        print("  Modbus/TCP ist offen -> sauberster Weg, ganz ohne Tuya.")
    if 6668 in open_ports:
        print("  Tuya-Port 6668 offen -> Steuerung moeglich, es fehlt nur der local_key.")
    if not web and 502 not in open_ports and 6668 not in open_ports:
        print("  Kein offener Steuer-Port gefunden.")
    print("\nSchick mir diese komplette Ausgabe, dann sage ich dir den naechsten Schritt.\n")


if __name__ == "__main__":
    main()
