"""Das Zeichen in der Adresszeile — und auf dem Home-Bildschirm.

Ohne Icon zeigt der Browser sein graues Ersatzsymbol, und iOS legt beim
„Zum Home-Bildschirm" einen Bildschirmausschnitt als Symbol ab. Bei einer
Seite, die man sich ans Handy heftet, ist das kein Schoenheitsfehler.
"""
import struct, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

print("--- Das SVG ist eines, und es ist klein ---")
svg = (ROOT / "web" / "favicon.svg").read_text("utf-8")
assert svg.lstrip().startswith("<svg") and "</svg>" in svg
assert len(svg) < 2000, f"{len(svg)} Byte — ein Reitersymbol braucht nicht mehr"
print(f"  favicon.svg, {len(svg)} Byte")

print("--- Das PNG ist ein echtes PNG in der Groesse, die iOS will ---")
roh = (ROOT / "web" / "icon-180.png").read_bytes()
assert roh[:8] == b"\x89PNG\r\n\x1a\n", "kein PNG"
breite, hoehe, tiefe, farbtyp = struct.unpack(">IIBB", roh[16:26])
assert (breite, hoehe) == (180, 180), (breite, hoehe)
assert farbtyp == 6, "RGBA, damit die runden Ecken durchsichtig sind"
print(f"  icon-180.png, {breite}x{hoehe}, RGBA, {len(roh)} Byte")

print("--- Jede Seite verweist darauf ---")
seiten = ("index.html", "ui.html", "wand.html", "display.html", "spiel.html")
for name in seiten:
    q = (ROOT / "web" / name).read_text("utf-8")
    assert '/favicon.svg' in q, f"{name} hat kein Icon"
    assert 'apple-touch-icon' in q, f"{name} hat kein Handy-Icon"
    assert q.index("/favicon.svg") < q.index("<title>"), \
        f"{name}: das Icon gehoert vor den Titel in den Kopf"
print("  " + ", ".join(seiten))

print("--- Und der Server liefert es aus ---")
q = (ROOT / "app.py").read_text("utf-8")
for pfad, typ in (("/favicon.svg", "image/svg+xml"),
                  ("/favicon.ico", "image/png"),
                  ("/icon-180.png", "image/png")):
    assert f'@app.get("{pfad}")' in q, f"{pfad} wird nicht ausgeliefert"
    print(f"  {pfad}  ->  {typ}")
# /favicon.ico fragen Browser von sich aus an, auch ohne <link>
assert 'favicon.ico' in q

print("\nAlle Icon-Tests bestanden.")
