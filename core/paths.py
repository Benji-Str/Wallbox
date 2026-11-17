"""Wo die veraenderlichen Daten liegen.

DIESE DATEI IST IN BEIDEN PROJEKTEN IDENTISCH (Wallbox-Steuerung und
GridMine-Steuerung). Aendert sie sich hier, gehoert sie drueben mitgezogen —
sie loest dasselbe Problem und soll nicht zweimal auseinanderlaufen.

Der Dienst setzt GM_DATA auf `<Installation>/data`. Startet jemand die
Software von Hand, ist die Variable NICHT gesetzt — frueher landete alles
dann direkt im Projektordner. Zwei verschiedene Orte fuer dieselbe Datei,
und beim naechsten Dienst-Neustart war scheinbar alles weg, obwohl es nur
woanders lag. Genau so sind die MQTT-Einstellungen „nach dem Update"
verschwunden; dasselbe haette drueben die Benutzerliste treffen koennen.

Deshalb: Ohne GM_DATA wird `<Projekt>/data` genommen — derselbe Ort, den auch
der Dienst benutzt. `uebernehmen()` holt eine an einem alten Ort liegende
Datei einmalig herueber.
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
    """Fruehere Ablageorte derselben Datei."""
    return [p / name for p in (ROOT, ROOT / "data") if (p / name) != (DATA / name)]


def uebernehmen(name: str, log=print) -> bool:
    """Eine an einem frueheren Ort liegende Datei in den Datenordner holen.

    Wer die Software einmal von Hand gestartet hat, hatte seine Daten danach
    an einer anderen Stelle als der Dienst. Beim naechsten Neustart —
    typischerweise nach einem Update — sah es aus, als waeren sie geloescht.
    Sie werden deshalb einmalig umgezogen statt stillschweigend ignoriert.

    Die alte Datei bleibt als `.alt` liegen: ein Umzug, der sich nicht
    rueckgaengig machen laesst, waere hier die falsche Vorsicht.
    """
    ziel = DATA / name
    if ziel.exists():
        return False
    for alt in alte_orte(name):
        if not alt.exists():
            continue
        try:
            ziel.write_bytes(alt.read_bytes())
            alt.rename(alt.with_suffix(alt.suffix + ".alt"))
            log(f"[paths] {name} aus {alt} uebernommen -> {ziel}")
            return True
        except Exception as e:
            log(f"[paths] {alt} konnte nicht uebernommen werden: {e}")
        return False
    return False


def venv_hinweis(paket: str = "tinytuya") -> str:
    """Klartext, wenn ein Paket fehlt — mit dem Python, der es wirklich hat.

    Der Dienst laeuft aus `<Installation>/.venv`, an der Konsole tippt man
    `python3`. Das ist ein anderer Python, und in dem fehlt jedes Paket aus
    `requirements.txt`. Die alte Meldung „pip install tinytuya" schickte
    deshalb in die Irre: Sie installiert ins System, wo es niemand sucht,
    und das Werkzeug geht danach immer noch nicht.
    """
    venv = ROOT / ".venv" / "bin" / "python3"
    if venv.exists():
        return (f"{paket} fehlt in diesem Python.\n"
                f"Der Dienst hat es — mit seinem Python starten:\n"
                f"  {venv} {' '.join(__import__('sys').argv) or '<werkzeug>'}")
    return (f"{paket} fehlt.  Installieren mit:\n"
            f"  pip install {paket}\n"
            f"(Auf einer Installation mit .venv: <Installation>/.venv/bin/pip)")
