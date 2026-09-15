"""Ladelog — abgeschlossene Ladevorgaenge, damit man hinterher sieht,
wieviel geladen wurde und in welchem Modus."""
from __future__ import annotations
import json, time
from pathlib import Path

from core.paths import DATA
FILE = DATA / "chargelog.json"
MAX_ENTRIES = 500


def _load() -> list:
    if not FILE.exists():
        return []
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def append_session(cp_id: str, s: dict):
    rows = _load()
    rows.append({"cp": cp_id, "start": s.get("start"), "end": s.get("end"),
                 "kwh": s.get("kwh", 0), "minutes": s.get("minutes", 0),
                 "mode": s.get("mode", ""), "peak_w": round(s.get("peak_w", 0)),
                 # "voll" = das Auto hat von selbst aufgehoert; nur solche
                 # Vorgaenge sagen etwas ueber die Akkugroesse aus
                 "ende": s.get("ende", ""),
                 # Zaehlerstaende Anfang/Ende — das ist, was eine Abrechnung
                 # belegen muss; "source" sagt, woher der Wert stammt.
                 "source": s.get("source", "wallbox"),
                 "meter_start": s.get("meter_start"), "meter_end": s.get("meter_end")})
    rows = rows[-MAX_ENTRIES:]
    try:
        FILE.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    except Exception as e:
        print(f"[chargelog] konnte nicht schreiben: {e}")


def list_sessions(cp_id: str = "", limit: int = 50) -> list:
    rows = [r for r in _load() if not cp_id or r.get("cp") == cp_id]
    return list(reversed(rows))[:limit]


def totals(cp_id: str = "") -> dict:
    rows = [r for r in _load() if not cp_id or r.get("cp") == cp_id]
    day = time.time() - 86400
    return {"count": len(rows),
            "kwh_total": round(sum(r.get("kwh", 0) for r in rows), 2),
            "kwh_24h": round(sum(r.get("kwh", 0) for r in rows
                                 if (r.get("end") or 0) >= day), 2)}
