"""Börsenstrompreise (Day-Ahead) für das Eco-Laden.

Quelle aWattar (EPEX Spot, kostenloses JSON):
    Österreich   https://api.awattar.at/v1/marketdata
    Deutschland  https://api.awattar.de/v1/marketdata

Der Marktpreis ist nicht der Preis, den du zahlst. Auf den Börsenpreis
kommen Netzgebühren, Abgaben und Steuern — deshalb `aufschlag_ct_kwh`. Ohne
diesen Aufschlag würde eine Schwelle von "5 ct" etwas völlig anderes bedeuten
als auf der Stromrechnung.

Die Preise gelten stundenweise und stehen für den nächsten Tag ab etwa 14 Uhr
fest. Bleibt der Abruf aus, werden die zuletzt geholten Preise weiter
verwendet, solange sie gelten — und danach keine, statt veraltete.
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field


@dataclass
class PreisConfig:
    quelle: str = "awattar_at"        # awattar_at | awattar_de | mock | aus
    aufschlag_ct_kwh: float = 0.0     # Netz, Abgaben, Steuern auf den Börsenpreis
    abruf_intervall_s: int = 1800


class PreisQuelle:
    def __init__(self, **kw):
        self.cfg = PreisConfig(**{k: v for k, v in kw.items()
                                  if k in PreisConfig.__dataclass_fields__})
        self._preise: list[tuple[float, float, float]] = []   # (von, bis, ct)
        self._geholt = 0.0
        self.letzter_fehler = ""

    # ------------------------------------------------------------------ Abruf
    async def hole(self, erzwingen: bool = False) -> bool:
        c = self.cfg
        if c.quelle == "aus":
            return False
        if not erzwingen and self._preise and \
           time.time() - self._geholt < c.abruf_intervall_s:
            return True
        if c.quelle == "mock":
            self._mock()
            return True
        url = ("https://api.awattar.de/v1/marketdata" if c.quelle == "awattar_de"
               else "https://api.awattar.at/v1/marketdata")
        try:
            import asyncio, json, urllib.request

            def laden():
                with urllib.request.urlopen(url, timeout=10) as r:
                    return json.loads(r.read())

            d = await asyncio.to_thread(laden)
            neu = []
            for z in d.get("data", []):
                # marketprice ist EUR/MWh -> /10 ergibt ct/kWh
                ct = float(z["marketprice"]) / 10.0 + c.aufschlag_ct_kwh
                neu.append((z["start_timestamp"] / 1000.0,
                            z["end_timestamp"] / 1000.0, ct))
            if neu:
                self._preise = sorted(neu)
                self._geholt = time.time()
                self.letzter_fehler = ""
                return True
            self.letzter_fehler = "Antwort ohne Preisdaten"
        except Exception as e:
            self.letzter_fehler = f"{type(e).__name__}: {e}"
        return bool(self._preise)

    def _mock(self):
        """Tagesgang mit Mittagsdelle und Abendspitze — zum Ausprobieren."""
        jetzt = time.time()
        beginn = jetzt - (jetzt % 3600)
        self._preise = []
        for h in range(36):
            stunde = time.localtime(beginn + h * 3600).tm_hour
            ct = {0:2.1,1:1.8,2:1.5,3:1.4,4:1.6,5:2.4,6:5.2,7:9.1,8:11.4,
                  9:9.8,10:6.2,11:3.4,12:1.1,13:0.4,14:1.9,15:4.1,16:7.3,
                  17:11.2,18:14.6,19:13.1,20:10.4,21:7.2,22:4.8,23:3.1}[stunde]
            self._preise.append((beginn + h*3600, beginn + (h+1)*3600,
                                 ct + self.cfg.aufschlag_ct_kwh))
        self._geholt = jetzt

    # --------------------------------------------------------------- Abfragen
    def aktuell(self, now: float | None = None):
        """Preis der laufenden Stunde, oder None wenn unbekannt."""
        now = now or time.time()
        for von, bis, ct in self._preise:
            if von <= now < bis:
                return round(ct, 2)
        return None

    def verlauf(self, stunden: int = 24, now: float | None = None) -> list:
        now = now or time.time()
        return [{"von": von, "ct": round(ct, 2)}
                for von, bis, ct in self._preise if bis > now][:stunden]

    def guenstigste(self, anzahl: float, fenster_h: int = 24,
                    now: float | None = None) -> list:
        """Die `anzahl` günstigsten Stunden im nächsten Fenster, zeitlich sortiert.

        Gedacht für "lade 6 Stunden, egal wann, aber möglichst billig".
        """
        now = now or time.time()
        kommende = [(von, bis, ct) for von, bis, ct in self._preise if bis > now]
        kommende = kommende[:max(1, int(fenster_h))]
        n = int(max(0, anzahl))
        if not n:
            return []
        billig = sorted(kommende, key=lambda x: x[2])[:n]
        return sorted(billig)

    def ist_guenstige_stunde(self, anzahl: float, fenster_h: int = 24,
                             now: float | None = None) -> bool:
        now = now or time.time()
        return any(von <= now < bis
                   for von, bis, _ in self.guenstigste(anzahl, fenster_h, now))

    def status(self) -> dict:
        return {"quelle": self.cfg.quelle,
                "aufschlag_ct_kwh": self.cfg.aufschlag_ct_kwh,
                "aktuell_ct": self.aktuell(),
                "stunden_bekannt": len(self.verlauf(48)),
                "zuletzt_geholt_vor_s": (round(time.time() - self._geholt)
                                         if self._geholt else None),
                "fehler": self.letzter_fehler}
