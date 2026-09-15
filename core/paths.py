"""Wo die veraenderlichen Daten liegen.

Im Container zeigt GM_DATA auf ein gemountetes Verzeichnis, damit Config,
Benutzer und Ladelog einen Neustart ueberleben. Ohne die Variable bleibt
alles im Projektordner wie bisher.
"""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("GM_DATA") or ROOT)
try:
    DATA.mkdir(parents=True, exist_ok=True)
except Exception:
    DATA = ROOT
