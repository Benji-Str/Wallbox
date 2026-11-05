"""Waechter: schreibt ALLES mit, was zwischen Box und Steuerung passiert.

Betreiber 05.11.2026: „Wir muessen einen Waechter einbauen, der alles liest, was
kommt." Die Box nimmt im lokalen Netz nur EINE Verbindung an — ein zweites
Programm, das mithoert, wuerde den Streit um die Verbindung messen statt der
Anlage (siehe CLAUDE.md). Darum sitzt der Waechter IM Dienst und schreibt mit,
was ohnehin durchlaeuft:

* ``box``       — jeder Datenpunkt, der sich an der Box geaendert hat (alt → neu)
* ``befehl``    — jeder Schreibbefehl an die Box, mit Erfolg/Fehlschlag
* ``schaltung`` — jeder Schaltvorgang mit Grund (auch Karte / Box selbst)
* ``regelung``  — jede Aenderung der Entscheidung (Modus, laden ja/nein, Grund, Karte)
* ``fehler``    — Box nicht erreichbar

Eine JSON-Zeile je Ereignis, eine Datei je Tag unter ``<GM_DATA>/waechter/``.
Aelter als ``TAGE_HALTEN`` wird weggeraeumt. Schreiben wirft nie — der Waechter
darf den Regeltakt nicht stoeren.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

TAGE_HALTEN = 30

# Diese Datenpunkte aendern sich laufend (Leistung, Phasenstroeme, Temperatur,
# Zaehlerstand). Sie werden mitgeschrieben, aber hoechstens alle WENIG_S Sekunden,
# damit die Datei lesbar bleibt — die Schaltvorgaenge gehen nie verloren.
LAUFEND = {"1", "6", "7", "8", "9", "24", "25"}
WENIG_S = 60.0


class Waechter:
    def __init__(self, ordner: Path, uhr=time.time):
        self.ordner = Path(ordner)
        self.uhr = uhr
        self._zuletzt: dict = {}            # dp -> ts der letzten Zeile (nur LAUFEND)
        self._aufgeraeumt = ""

    # ------------------------------------------------------------------ Schreiben
    def schreiben(self, art: str, **felder) -> None:
        try:
            jetzt = self.uhr()
            tag = time.strftime("%Y-%m-%d", time.localtime(jetzt))
            self.ordner.mkdir(parents=True, exist_ok=True)
            zeile = json.dumps({"ts": round(jetzt, 2), "art": art, **felder},
                               ensure_ascii=False, default=str)
            with open(self.ordner / f"{tag}.jsonl", "a", encoding="utf-8") as f:
                f.write(zeile + "\n")
            if self._aufgeraeumt != tag:
                self._aufgeraeumt = tag
                self._aufraeumen(jetzt)
        except Exception:
            pass                                # nie den Regeltakt stoeren

    def box(self, alt: dict, neu: dict) -> None:
        """Vergleicht das Bild vorher/nachher und schreibt jede Aenderung."""
        jetzt = self.uhr()
        for dp, wert in neu.items():
            dp = str(dp)
            if dp.startswith("_") or alt.get(dp) == wert:
                continue
            if dp in LAUFEND:
                if jetzt - self._zuletzt.get(dp, 0.0) < WENIG_S:
                    continue
                self._zuletzt[dp] = jetzt
            self.schreiben("box", dp=dp, alt=alt.get(dp), neu=wert)

    def _aufraeumen(self, jetzt: float) -> None:
        grenze = time.strftime("%Y-%m-%d", time.localtime(jetzt - TAGE_HALTEN * 86400))
        for p in self.ordner.glob("*.jsonl"):
            if p.stem < grenze:
                try:
                    p.unlink()
                except OSError:
                    pass

    # ------------------------------------------------------------------ Lesen
    def lesen(self, stunden: float = 6.0) -> list:
        """Die Ereignisse der letzten `stunden`, aelteste zuerst."""
        ab = self.uhr() - stunden * 3600
        aus = []
        for p in sorted(self.ordner.glob("*.jsonl"))[-(int(stunden // 24) + 2):]:
            try:
                for z in p.read_text(encoding="utf-8").splitlines():
                    try:
                        e = json.loads(z)
                    except ValueError:
                        continue
                    if e.get("ts", 0) >= ab:
                        aus.append(e)
            except OSError:
                continue
        return aus


DP_NAMEN = {"1": "Zaehlerstand", "3": "Zustand", "4": "Ladestrom A", "6": "Strom L1",
            "7": "Strom L2", "8": "Strom L3", "9": "Leistung W", "10": "Stoerung",
            "13": "Fahrzeug (CP)", "14": "Betriebsart", "18": "SCHALTER",
            "23": "Firmware", "24": "Temperatur", "25": "Energie Vorgang"}


def als_text(eintraege: list) -> str:
    """Klartext fuer den Browser — eine Zeile je Ereignis."""
    zeilen = []
    for e in eintraege:
        zeit = time.strftime("%d.%m. %H:%M:%S", time.localtime(e.get("ts", 0)))
        art = e.get("art")
        if art == "box":
            name = DP_NAMEN.get(str(e.get("dp")), f"DP{e.get('dp')}")
            zeilen.append(f"{zeit}  BOX       {name}: {e.get('alt')} -> {e.get('neu')}")
        elif art == "befehl":
            name = DP_NAMEN.get(str(e.get("dp")), f"DP{e.get('dp')}")
            ok = "ok" if e.get("ok") else "FEHLGESCHLAGEN"
            zeilen.append(f"{zeit}  BEFEHL    {name} = {e.get('wert')}  ({ok})")
        elif art == "schaltung":
            wer = "KARTE/BOX" if e.get("selbst") else "STEUERUNG"
            zeilen.append(f"{zeit}  SCHALTUNG {'EIN' if e.get('ein') else 'AUS'} durch {wer}"
                          f"{'' if e.get('ok', True) else ' FEHLGESCHLAGEN'} — {e.get('grund', '')}")
        elif art == "regelung":
            zeilen.append(f"{zeit}  REGELUNG  Modus {e.get('modus')} · laden: {e.get('laden')}"
                          f"{' · KARTE' if e.get('karte') else ''} — {e.get('grund', '')}")
        elif art == "fehler":
            zeilen.append(f"{zeit}  FEHLER    {e.get('text', '')}")
        else:
            zeilen.append(f"{zeit}  {art}  {e}")
    return "\n".join(zeilen) if zeilen else "— in diesem Zeitraum nichts aufgezeichnet —"
