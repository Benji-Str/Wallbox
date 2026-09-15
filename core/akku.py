"""Akkugroesse aus den Ladevorgaengen lernen.

Die Steuerung kennt den Ladestand des Fahrzeugs NICHT. Sie weiss nur, wieviel
Energie sie geliefert hat. Aus einer einzelnen Ladung laesst sich die
Akkugroesse deshalb nicht bestimmen.

Was sich bestimmen laesst: wieviel das Auto **am Stueck angenommen** hat,
bevor es von selbst aufgehoert hat. Das ist eine Untergrenze der nutzbaren
Kapazitaet — und zwar eine, die mit jeder Ladung nur genauer werden kann:

    Kapazitaet  >=  gelieferte Energie x Ladewirkungsgrad

Der Wirkungsgrad steht drin, weil beim Wechselstromladen im Auto Verluste
anfallen (Ladegeraet, Waerme, Batteriemanagement). Aus der Steckdose kommt
mehr, als im Akku ankommt — ohne diesen Abschlag waere die "Untergrenze"
zu hoch und damit keine.

Warum es eine Untergrenze bleibt und nie eine Zusage wird:
  * Wer bei 50 % ansteckt, laedt nur die halbe Kapazitaet nach.
  * Viele Fahrzeuge laden ab Werk nur bis 80 %.
  * Ein Abfahrtszeitplan im Auto kann frueher beenden.
Deshalb wird der groesste beobachtete Wert genommen, nicht der Mittelwert,
und deshalb heisst es in der Anzeige "mindestens".
"""
from __future__ import annotations

WIRKUNGSGRAD = 0.88          # AC ab Wallbox -> im Akku angekommen
MIN_KWH = 0.5                # darunter ist es kein Ladevorgang, sondern Rauschen


def lerne(sessions: list, wirkungsgrad: float = WIRKUNGSGRAD) -> dict:
    """`sessions` wie aus core.chargelog. Rueckgabe ist ein Urteil, kein Wert:
    es sagt immer mit dazu, worauf es beruht."""
    voll = [s for s in sessions
            if s.get("ende") == "voll" and (s.get("kwh") or 0) >= MIN_KWH]
    if not voll:
        # Auch ohne vollstaendige Ladung ist die groesste bisherige Ladung
        # eine harte Untergrenze — nur eine schwaechere.
        alle = [s for s in sessions if (s.get("kwh") or 0) >= MIN_KWH]
        if not alle:
            return {"kwh": None, "sicherheit": "keine",
                    "anzahl": 0, "groesste_kwh": None,
                    "text": "Noch keine Ladung aufgezeichnet."}
        best = max(alle, key=lambda s: s["kwh"])
        return {"kwh": round(best["kwh"] * wirkungsgrad, 1),
                "sicherheit": "schwach", "anzahl": 0,
                "groesste_kwh": round(best["kwh"], 2), "letzte": best.get("end"),
                "text": (f"Mindestens {best['kwh'] * wirkungsgrad:.1f} kWh — "
                         f"aus der groessten bisherigen Ladung "
                         f"({best['kwh']:.2f} kWh). Das Auto hat dabei noch "
                         f"nie von selbst aufgehoert, der Wert kann also "
                         f"deutlich zu klein sein.")}
    best = max(voll, key=lambda s: s["kwh"])
    n = len(voll)
    # Mit mehreren vollstaendigen Ladungen, die nah beieinander liegen, ist
    # der Wert belastbar; streuen sie stark, wurde offenbar unterschiedlich
    # voll angesteckt und nur der groesste Wert zaehlt.
    zweit = sorted((s["kwh"] for s in voll), reverse=True)[1] if n > 1 else 0.0
    nah = n > 1 and zweit >= best["kwh"] * 0.9
    sicherheit = "gut" if (n >= 3 and nah) else "mittel" if n >= 2 else "schwach"
    kwh = round(best["kwh"] * wirkungsgrad, 1)
    return {"kwh": kwh, "sicherheit": sicherheit, "anzahl": n,
            "groesste_kwh": round(best["kwh"], 2), "letzte": best.get("end"),
            "text": (f"Mindestens {kwh:.1f} kWh — das Fahrzeug hat "
                     f"{best['kwh']:.2f} kWh am Stueck angenommen und dann "
                     f"von selbst aufgehoert ({n} solche "
                     f"{'Ladung' if n == 1 else 'Ladungen'}). "
                     + ("Die Werte liegen eng beieinander, der Akku duerfte "
                        "kaum groesser sein." if nah else
                        "Wurde nie ganz leer angesteckt, ist der Akku groesser."))}
