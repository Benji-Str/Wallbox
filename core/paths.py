"""Wo die veraenderlichen Daten liegen.

Der Dienst setzt GM_DATA auf `<Installation>/data`. Startet jemand die
Steuerung von Hand (`python3 app.py`), ist die Variable NICHT gesetzt —
frueher landete die Konfiguration dann direkt im Projektordner. Zwei
verschiedene Orte fuer dieselbe Datei, und beim naechsten Dienst-Neustart
war scheinbar alles weg, obwohl es nur woanders lag. Genau das ist als
„nach dem Update sind die MQTT-Werte weg" aufgefallen.

Deshalb: Ohne GM_DATA wird `<Projekt>/data` genommen — derselbe Ort, den
auch der Dienst benutzt. `alte_orte()` nennt die frueheren Ablagen, damit
eine vorhandene Konfiguration beim Start uebernommen werden kann.
"""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _waehle() -> Path:
    gesetzt = os.environ.get("GM_DATA")
    for kandidat in ([Path(gesetzt)] if gesetzt else []) + [ROOT / "data", ROOT]:
        try:
            kandidat.mkdir(parents=True, exist_ok=True)
            return kandidat
        except Exception:
            continue
    return ROOT


DATA = _waehle()


def alte_orte(name: str) -> list:
    """Fruehere Ablageorte derselben Datei — fuer die Uebernahme beim Start."""
    return [p / name for p in (ROOT, ROOT / "data") if (p / name) != (DATA / name)]
