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


# ------------------------------------------------------------- Startauswertung
# Die eigentliche Frage der Anlage lautet seit Wochen: Warum nimmt das Fahrzeug
# einen Start der Steuerung nicht an, einen mit der Karte aber doch? Der Waechter
# schreibt alles mit, was man dafuer braucht — aber in einer Wand aus Zeilen.
# Diese Auswertung stellt die Starts nebeneinander und nennt je Start, was
# geschrieben wurde und was das Fahrzeug danach getan hat.
FENSTER_S = 300.0       # so lange nach einem Start wird noch zugeordnet
VOR_S = 15.0            # Befehle so kurz vor dem Einschalten gehoeren dazu
LAEDT_W = 500.0         # darunter laedt nichts, das ist Eigenverbrauch der Box


def _wer(e: dict) -> str:
    return "KARTE/BOX" if e.get("selbst") else "STEUERUNG"


def startbericht(eintraege: list, jetzt: float = None) -> str:
    """Je Einschaltvorgang: wer, was geschrieben wurde, was das Auto tat."""
    jetzt = time.time() if jetzt is None else jetzt
    ev = sorted(eintraege, key=lambda e: e.get("ts", 0))
    starts = [i for i, e in enumerate(ev)
              if e.get("art") == "schaltung" and e.get("ein") and e.get("ok", True)]
    if not starts:
        return ("\u2014 in diesem Zeitraum wurde nicht eingeschaltet \u2014\n\n"
                "Ohne einen Einschaltvorgang ist nichts zu vergleichen. Einmal mit der\n"
                "Karte und einmal in der Oberflaeche starten, dann diese Seite erneut.")

    # Laufendes Bild der Datenpunkte: Der Waechter schreibt nur Aenderungen mit.
    # Ohne dieses Bild hiesse ein Start ohne neue CP-Meldung "unveraendert" — und
    # das ist keine Auskunft, sondern ein Achselzucken. Bekannt ist, was zuletzt
    # gemeldet wurde; unbekannt bleibt unbekannt und wird auch so genannt.
    bild, vor_start = {}, {}
    for i, e in enumerate(ev):
        if i in starts:
            vor_start[i] = dict(bild)
        if e.get("art") == "box":
            bild[str(e.get("dp"))] = e.get("neu")

    zeilen, bilanz = [], {}
    for nr, i in enumerate(starts, 1):
        s = ev[i]
        t0 = s.get("ts", 0)
        davor = vor_start.get(i, {})
        # Das Ausschalten wird OHNE Zeitgrenze gesucht. Mit einem Fenster von
        # fuenf Minuten fehlte genau der Fall, um den es geht: eine Ladung, die
        # nach zwanzig Minuten abbricht. Wer nicht hinsieht, findet nichts.
        ende, ende_e = jetzt, None
        for e in ev[i + 1:]:
            if e.get("art") == "schaltung":
                if e.get("ein"):
                    ende = e.get("ts", 0)      # naechster Start: hier endet unserer
                    break
                ende, ende_e = e.get("ts", 0), e
                break
        befehle = [e for e in ev[:i + 1]
                   if e.get("art") == "befehl" and t0 - VOR_S <= e.get("ts", 0) <= t0]
        spitze, cp, zustaende = None, [], []
        for e in ev[i + 1:]:
            if e.get("ts", 0) > ende:
                break
            if e.get("art") != "box":
                continue
            if str(e.get("dp")) == "9":
                try:
                    spitze = max(spitze or 0.0, float(e.get("neu") or 0))
                except (TypeError, ValueError):
                    pass
            elif str(e.get("dp")) == "13":
                cp.append(str(e.get("neu")))
            elif str(e.get("dp")) == "3":
                zustaende.append(str(e.get("neu")))

        cp_jetzt = cp[-1] if cp else davor.get("13")
        zu_jetzt = zustaende[-1] if zustaende else davor.get("3")
        laeuft = ende_e is None and jetzt < t0 + FENSTER_S
        if spitze is not None and spitze >= LAEDT_W:
            urteil = f"GELADEN \u2014 Spitze {spitze:.0f} W"
            geladen = True
        elif laeuft:
            urteil = (f"LAEUFT NOCH \u2014 erst {jetzt - t0:.0f} s her, das Fahrzeug "
                      f"handelt noch aus (bis {FENSTER_S:.0f} s warten)")
            geladen = None
        elif str(cp_jetzt or "").find("6v") >= 0 or "charging" in str(zu_jetzt or ""):
            urteil = "angenommen (6 V), aber keine Leistung aufgezeichnet"
            geladen = False
        elif cp_jetzt is None and spitze is None:
            urteil = ("UNKLAR \u2014 die Box hat in diesem Fenster weder Control Pilot "
                      "noch Leistung gemeldet")
            geladen = None
        else:
            urteil = f"NICHT ANGENOMMEN \u2014 Control Pilot blieb bei {cp_jetzt or 'unbekannt'}"
            geladen = False
        wer = _wer(s)
        if geladen is not None:
            bilanz.setdefault(wer, []).append(geladen)

        zeilen.append(f"\n{nr}. {time.strftime('%d.%m. %H:%M:%S', time.localtime(t0))}"
                      f"  EIN durch {wer} \u2014 {s.get('grund', '')}")
        if befehle:
            for b in befehle:
                name = DP_NAMEN.get(str(b.get("dp")), f"DP{b.get('dp')}")
                zeilen.append(f"     geschrieben: {name} = {b.get('wert')}"
                              f"{'' if b.get('ok') else '  FEHLGESCHLAGEN'}")
        else:
            zeilen.append("     geschrieben: nichts von uns (Karte oder Box selbst)")
        zeilen.append("     davor:       Fahrzeug "
                      f"{davor.get('13') or 'unbekannt'} \u00b7 Zustand "
                      f"{davor.get('3') or 'unbekannt'} \u00b7 Ladestrom "
                      f"{davor.get('4') if davor.get('4') is not None else 'unbekannt'} A")
        if cp:
            zeilen.append(f"     Fahrzeug:    {' -> '.join(cp[:6])}")
        else:
            zeilen.append("     Fahrzeug:    keine neue Meldung \u2014 blieb bei "
                          f"{davor.get('13') or 'unbekannt'}")
        if zustaende:
            zeilen.append(f"     Zustand:     {' -> '.join(zustaende[:6])}")
        zeilen.append("     Leistung:    "
                      + (f"Spitze {spitze:.0f} W" if spitze is not None
                         else "keine Leistungsmeldung in diesem Fenster"))
        if ende_e is not None:
            dauer = ende - t0
            wie = (f"{dauer / 60:.0f} min" if dauer >= 120 else f"{dauer:.0f} s")
            zeilen.append(f"     AUS nach {wie} durch {_wer(ende_e)}"
                          f" \u2014 {ende_e.get('grund', '')}")
        elif not laeuft:
            zeilen.append(f"     laeuft seit {(jetzt - t0) / 60:.0f} min, kein Aus aufgezeichnet")
        zeilen.append(f"     URTEIL:      {urteil}")

    kopf = ["Starts in diesem Zeitraum: " + (", ".join(
        f"{w} {sum(v)} von {len(v)} geladen" for w, v in sorted(bilanz.items()))
        or "noch keiner abgeschlossen")]
    karte = bilanz.get("KARTE/BOX", [])
    uns = bilanz.get("STEUERUNG", [])
    if karte and uns and any(karte) and not any(uns):
        kopf.append("\nDie Karte laedt, die Steuerung nicht \u2014 der Unterschied steht oben in den")
        kopf.append("Zeilen 'geschrieben'. Alles, was dort bei der Karte NICHT steht, ist ein")
        kopf.append("Verdaechtiger: die Box nimmt nach diesen Befehlen den Schuetz anders an.")
    elif uns and all(uns):
        kopf.append("\nJeder Start der Steuerung hat geladen \u2014 die Oberflaeche steuert durch.")
    elif not uns:
        kopf.append("\nDie Steuerung hat in diesem Zeitraum nicht eingeschaltet. Zum Vergleich")
        kopf.append("fehlt ihr Start: einmal in der Oberflaeche auf 'Sofort' druecken.")
    if karte and not any(karte):
        kopf.append("\nAuch mit der Karte wurde nicht geladen. Dann liegt es nicht an der")
        kopf.append("Steuerung \u2014 sie hat nichts geschrieben. Steckt das Kabel am Auto?")
        kopf.append("Steht das Fahrzeug im Ladefehler, hilft nur ab- und wieder anstecken.")
    return "\n".join(kopf) + "\n" + "\n".join(zeilen)
