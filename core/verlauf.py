"""Kurzzeitgedaechtnis der Messwerte — fuer die Balken am Display.

Bewusst nur im Arbeitsspeicher und bewusst kurz. Eine richtige Zeitreihe
gehoert in eine Datenbank; das Display braucht nur zu zeigen, was in der
letzten Stunde war. Nach einem Neustart ist der Verlauf leer — das ist
richtig so und nicht der Rede wert.

Unbekannt bleibt unbekannt: fehlt ein Wert, wird er nicht zu 0 gemittelt,
sondern ausgelassen. Ein Balken, der Null zeigt, obwohl niemand gemessen
hat, erzaehlt eine Geschichte, die nicht stimmt.
"""
from __future__ import annotations
import time
from collections import deque

FELDER = ("pv", "netz", "haus", "laden", "soc")


class Verlauf:
    def __init__(self, punkte: int = 360, abstand_s: float = 10.0,
                 clock=time.time):
        self.punkte = max(2, int(punkte))
        self.abstand_s = max(0.0, float(abstand_s))
        self._clock = clock
        self._d: deque = deque(maxlen=self.punkte)
        self._letzt: float | None = None

    def merke(self, **werte) -> bool:
        """Einen Messpunkt ablegen. Zu dicht aufeinander folgende Aufrufe
        werden verworfen, damit ein kurzer Regeltakt den Verlauf nicht auf
        wenige Minuten zusammenschrumpft."""
        now = self._clock()
        if self._letzt is not None and now - self._letzt < self.abstand_s:
            return False
        self._letzt = now
        p = {"t": now}
        for f in FELDER:
            v = werte.get(f)
            p[f] = None if v is None else float(v)
        self._d.append(p)
        return True

    def __len__(self) -> int:
        return len(self._d)

    # ------------------------------------------------------------------
    def reihen(self, n: int = 40) -> dict:
        """Auf n Balken eindampfen. Jeder Balken ist der Mittelwert seiner
        Messpunkte; ohne bekannten Wert bleibt er None."""
        n = max(1, min(240, int(n)))
        d = list(self._d)
        if not d:
            return {"n": 0, "von": None, "bis": None,
                    **{f: [] for f in FELDER}}
        # Weniger Messpunkte als Balken: dann gibt es eben weniger Balken.
        # Sonst stuenden zwischen drei Werten 37 Luecken.
        n = min(n, len(d))
        eimer: list[list[dict]] = [[] for _ in range(n)]
        # Nach Position aufteilen, nicht nach Zeit: bei ausgefallenen Takten
        # waeren zeitgleiche Eimer teils leer und die Balken luecken.
        for i, p in enumerate(d):
            eimer[min(n - 1, i * n // len(d))].append(p)
        out: dict = {"n": n, "von": d[0]["t"], "bis": d[-1]["t"]}
        for f in FELDER:
            reihe = []
            for e in eimer:
                vals = [p[f] for p in e if p[f] is not None]
                reihe.append(round(sum(vals) / len(vals), 1) if vals else None)
            out[f] = reihe
        return out
