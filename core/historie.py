"""Dauerhafte Messwert-Aufzeichnung — Verbrauch, Erzeugung, Ladezeiten.

Der Verlauf in `core/verlauf.py` liegt nur im Arbeitsspeicher und ist nach
einem Neustart weg. Das reicht fuer die laufenden Balken am Display, nicht
aber fuer die Frage „wieviel hat das Auto im Maerz gekostet".

Zwei Dinge werden getrennt gefuehrt, weil sie unterschiedlich altern:

  * **Messpunkte** (Watt) — eine Zeile je Minute in `verlauf/JJJJ-MM-TT.csv`.
    Grob 60 kB am Tag. Alte Tage werden nach `tage_behalten` geloescht.
  * **Tagesenergie** (kWh) — eine Zeile je Tag in `tagesenergie.json`.
    Winzig, wird nie geloescht. Das ist der Teil, den man in zwei Jahren
    noch sehen will.

Die Energie wird bei jedem Takt aufintegriert (W · s), nicht aus den
Minutenwerten nachgerechnet — sonst wuerde jede ausgelassene Minute fehlen.

Fehlende Messwerte werden NICHT als 0 verbucht. Stattdessen laeuft je Feld
eine Luecken-Uhr mit; die Oberflaeche kann damit sagen „an diesem Tag fehlen
20 Minuten" statt eine zu kleine Zahl als Wahrheit auszugeben.
"""
from __future__ import annotations
import json, time
from datetime import datetime, date
from pathlib import Path

# Was gemessen wird. netz/akku sind vorzeichenbehaftet und werden fuer die
# Energie in zwei Richtungen aufgeteilt.
FELDER = ("pv", "netz", "haus", "laden", "akku", "soc")
ENERGIE = ("pv", "bezug", "einspeisung", "haus", "laden",
           "akku_laden", "akku_entladen")


def _tag(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


class Historie:
    def __init__(self, ordner, tage_behalten: int = 400,
                 aufloesung_s: float = 60.0, clock=time.time):
        self.ordner = Path(ordner)
        self.verlauf_dir = self.ordner / "verlauf"
        self.energie_datei = self.ordner / "tagesenergie.json"
        self.tage_behalten = int(tage_behalten)
        self.aufloesung_s = float(aufloesung_s)
        self._clock = clock
        self._letzt_t: float | None = None     # letzte Integration
        self._letzt_zeile: float | None = None  # letzte geschriebene Minute
        self._tag = ""
        self._energie: dict = {}               # Tageswerte aus der Datei
        self._heute: dict = {}
        self.fehler = ""
        self._lade_energie()

    # ------------------------------------------------------------------
    def _lade_energie(self):
        try:
            if self.energie_datei.exists():
                self._energie = json.loads(self.energie_datei.read_text("utf-8"))
        except Exception as e:
            # Eine kaputte Statistik darf die Regelung nicht aufhalten.
            print(f"[historie] {self.energie_datei} unlesbar ({e}) — fange neu an")
            self._energie = {}

    def _tagesatz(self, tag: str) -> dict:
        d = self._energie.get(tag)
        if d is None:
            d = {k: 0.0 for k in ENERGIE}
            d["luecken_s"] = 0.0
            self._energie[tag] = d
        for k in ENERGIE:                      # aeltere Dateien nachziehen
            d.setdefault(k, 0.0)
        d.setdefault("luecken_s", 0.0)
        return d

    # ------------------------------------------------------------------
    def merke(self, **werte) -> None:
        """Einen Takt verbuchen. Watt rein, kWh und Minutenzeilen raus."""
        now = self._clock()
        tag = _tag(now)
        if tag != self._tag:
            # Tageswechsel: die Integration darf nicht ueber Mitternacht
            # hinweg auf den neuen Tag gebucht werden.
            self._tag = tag
            self._letzt_t = None
            self._heute = self._tagesatz(tag)
            self._aufraeumen()

        dt = 0.0 if self._letzt_t is None else now - self._letzt_t
        self._letzt_t = now
        # Nach Neustart oder Standby klafft eine Luecke. Sie darf nicht mit
        # dem letzten Messwert gefuellt werden — das erfindet Energie.
        if 0 < dt <= max(120.0, self.aufloesung_s * 3):
            self._integriere(dt, werte)
        elif dt > 0:
            self._heute["luecken_s"] = round(self._heute.get("luecken_s", 0.0) + dt, 1)

        if self._letzt_zeile is None or now - self._letzt_zeile >= self.aufloesung_s:
            self._letzt_zeile = now
            self._schreibe_zeile(now, werte)
            self._schreibe_energie()

    def _integriere(self, dt: float, werte: dict):
        h, f = self._heute, dt / 3_600_000.0    # W·s -> kWh
        fehlt = False
        for name, key in (("pv", "pv"), ("haus", "haus"), ("laden", "laden")):
            v = werte.get(name)
            if v is None:
                fehlt = fehlt or name in ("pv", "haus")
                continue
            h[key] = round(h[key] + float(v) * f, 4)
        netz = werte.get("netz")
        if netz is None:
            fehlt = True
        else:
            # Vorzeichen: positiv = Bezug, negativ = Einspeisung. Beide
            # getrennt zaehlen — die Summe allein sagt nichts ueber Kosten.
            if netz >= 0:
                h["bezug"] = round(h["bezug"] + netz * f, 4)
            else:
                h["einspeisung"] = round(h["einspeisung"] - netz * f, 4)
        akku = werte.get("akku")
        if akku is not None:
            if akku >= 0:
                h["akku_laden"] = round(h["akku_laden"] + akku * f, 4)
            else:
                h["akku_entladen"] = round(h["akku_entladen"] - akku * f, 4)
        if fehlt:
            h["luecken_s"] = round(h.get("luecken_s", 0.0) + dt, 1)

    # ------------------------------------------------------------------
    def _schreibe_zeile(self, now: float, werte: dict):
        z = [str(int(now))]
        for f in FELDER:
            v = werte.get(f)
            z.append("" if v is None else str(round(float(v), 1)))
        try:
            self.verlauf_dir.mkdir(parents=True, exist_ok=True)
            datei = self.verlauf_dir / f"{self._tag}.csv"
            neu = not datei.exists()
            with datei.open("a", encoding="utf-8") as fh:
                if neu:
                    fh.write("t," + ",".join(FELDER) + "\n")
                fh.write(",".join(z) + "\n")
            self.fehler = ""
        except Exception as e:
            self.fehler = f"Verlauf nicht schreibbar: {e}"

    def _schreibe_energie(self):
        try:
            self.ordner.mkdir(parents=True, exist_ok=True)
            self.energie_datei.write_text(
                json.dumps(self._energie, indent=1, sort_keys=True), encoding="utf-8")
        except Exception as e:
            self.fehler = f"Tagesenergie nicht schreibbar: {e}"

    def _aufraeumen(self):
        """Alte Minutendateien loeschen. Die Tagesenergie bleibt."""
        if self.tage_behalten <= 0 or not self.verlauf_dir.exists():
            return
        try:
            dateien = sorted(self.verlauf_dir.glob("*.csv"))
            # Den heutigen Tag mitzaehlen, auch wenn seine Datei noch nicht
            # existiert — sonst waere es je nach Reihenfolge ein Tag zuviel.
            behalten = set(sorted({p.stem for p in dateien} | {self._tag})
                           [-self.tage_behalten:])
            for p in dateien:
                if p.stem not in behalten:
                    p.unlink()
        except Exception as e:
            print(f"[historie] Aufraeumen fehlgeschlagen: {e}")

    # ------------------------------------------------------------------
    def tag(self, datum: str = "", punkte: int = 288) -> dict:
        """Messpunkte eines Tages, auf `punkte` Stuetzstellen eingedampft."""
        datum = datum or _tag(self._clock())
        datei = self.verlauf_dir / f"{datum}.csv"
        reihen: list = []
        try:
            with datei.open(encoding="utf-8") as fh:
                kopf = fh.readline().strip().split(",")
                for zeile in fh:
                    teile = zeile.rstrip("\n").split(",")
                    if len(teile) != len(kopf):
                        continue           # abgeschnittene letzte Zeile
                    try:
                        p = {"t": float(teile[0])}
                    except ValueError:
                        continue
                    for i, name in enumerate(kopf[1:], start=1):
                        try:
                            p[name] = float(teile[i]) if teile[i] != "" else None
                        except ValueError:
                            p[name] = None
                    reihen.append(p)
        except FileNotFoundError:
            pass
        except Exception as e:
            return {"datum": datum, "n": 0, "fehler": str(e),
                    **{f: [] for f in FELDER}, "t": []}
        return {"datum": datum, "energie": self._energie.get(datum),
                **_eindampfen(reihen, punkte)}

    def tage(self, n: int = 30) -> list:
        """Tagesenergie, neuester Tag zuletzt — so laufen die Balken von
        links nach rechts wie ein Kalender."""
        return [{"tag": t, **self._energie[t]}
                for t in sorted(self._energie)[-max(1, int(n)):]]

    def heute(self) -> dict:
        t = _tag(self._clock())
        return {"tag": t, **self._tagesatz(t)}

    def status(self) -> dict:
        return {"tage": len(self._energie), "ordner": str(self.verlauf_dir),
                "aufloesung_s": self.aufloesung_s,
                "tage_behalten": self.tage_behalten, "fehler": self.fehler}


def _eindampfen(reihen: list, punkte: int) -> dict:
    punkte = max(1, min(1440, int(punkte)))
    if not reihen:
        return {"n": 0, "t": [], **{f: [] for f in FELDER}}
    punkte = min(punkte, len(reihen))
    eimer: list[list] = [[] for _ in range(punkte)]
    for i, p in enumerate(reihen):
        eimer[min(punkte - 1, i * punkte // len(reihen))].append(p)
    out: dict = {"n": punkte,
                 "t": [round(e[0]["t"]) for e in eimer]}
    for f in FELDER:
        reihe = []
        for e in eimer:
            vals = [p.get(f) for p in e if p.get(f) is not None]
            reihe.append(round(sum(vals) / len(vals), 1) if vals else None)
        out[f] = reihe
    return out
