"""Laderegelung fuer Wallboxen — Lademodi nach dem Vorbild von openWB.

Warum eigene Logik statt des Miner-Regulators: Ein Miner darf jederzeit
gedrosselt werden, ein Ladevorgang nicht. Deshalb arbeitet diese Regelung mit
Schwellen UND Zeitverzoegerungen — eine Wolke fuer 30 s darf das Laden nicht
abbrechen. Der Miner-Regulator kennt nur ein Deadband, das reicht hier nicht.

Lademodi:
  stop    — aus
  sofort  — laedt sofort mit festem Strom, unabhaengig von der PV
  pv      — nur Ueberschuss; startet/stoppt ueber Schwelle + Verzoegerung
  minpv   — immer mindestens Mindeststrom, Ueberschuss kommt obendrauf
  ziel    — bis Zeitpunkt X die gewuenschte Energie, PV bevorzugt,
            Netzbezug erst wenn es sonst nicht mehr reicht

Die Regelung rechnet in Watt. Die Umrechnung auf Ampere macht der Treiber,
der die Geraetegrenzen kennt (beim OS-EC01 8-32 A).
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field

MODES = ("stop", "sofort", "pv", "minpv", "ziel")


@dataclass
class ChargeConfig:
    mode: str = "pv"
    # -- Sofortladen --
    sofort_a: int = 16
    # -- PV-Laden: Schwellen mit Verzoegerung --
    einschalt_w: int = 1400          # so viel Ueberschuss zum Starten
    einschalt_delay_s: int = 120     # und zwar so lange am Stueck
    ausschalt_w: int = 0             # darunter stoppen (darf negativ sein)
    ausschalt_delay_s: int = 180
    # -- Min+PV --
    min_a: int = 8
    # -- Zielladen --
    ziel_kwh: float = 0.0
    ziel_time: str = ""              # "07:00"
    # -- Rahmenbedingungen --
    reserve_w: int = 0               # Puffer, der nicht verladen wird
    max_total_w: int = 0             # Hausanschluss-Grenze (0 = aus)


@dataclass
class ChargeState:
    charging: bool = False
    target_w: float = 0.0
    reason: str = "init"
    mode: str = "pv"
    since: float = 0.0               # seit wann laeuft/pausiert es
    session_kwh: float = 0.0
    _above: float = 0.0              # seit wann ueber Einschaltschwelle
    _below: float = 0.0              # seit wann unter Ausschaltschwelle


class ChargeController:
    """Eine Ladestation. Bekommt pro Takt den Ueberschuss und sagt,
    mit wieviel Watt geladen werden soll."""

    def __init__(self, cfg: ChargeConfig, min_w: int, max_w: int,
                 w_per_a: int, clock=time.time):
        self.cfg = cfg
        self.min_w = min_w            # Geraete-Minimum (8 A) in Watt
        self.max_w = max_w
        self.w_per_a = w_per_a
        self.state = ChargeState(mode=cfg.mode)
        self._clock = clock

    # ------------------------------------------------------------------
    def tick(self, surplus_w: float, charge_w: float = 0.0,
             plugged: bool = True, session_kwh: float = 0.0) -> ChargeState:
        """surplus_w: verfuegbarer Ueberschuss INKLUSIVE dessen, was die
        Wallbox gerade schon zieht (sonst wuerde sie sich selbst wegregeln)."""
        s = self.state
        s.mode = self.cfg.mode
        s.session_kwh = session_kwh
        now = self._clock()

        if not plugged:
            return self._set(False, 0, "kein Fahrzeug")
        if self.cfg.mode == "stop":
            return self._set(False, 0, "Modus Stop")

        avail = surplus_w - self.cfg.reserve_w

        if self.cfg.mode == "sofort":
            w = self._cap(self.cfg.sofort_a * self.w_per_a)
            return self._set(True, w, f"Sofortladen {self.cfg.sofort_a} A")

        if self.cfg.mode == "minpv":
            # Mindeststrom immer, Ueberschuss obendrauf. Schwellen und
            # Verzoegerungen gelten hier bewusst nicht (wie bei openWB).
            floor = self.cfg.min_a * self.w_per_a
            w = self._cap(max(floor, avail))
            return self._set(True, w, f"Min+PV (mind. {self.cfg.min_a} A)")

        if self.cfg.mode == "ziel":
            return self._ziel(avail, now)

        return self._pv(avail, now)

    # ------------------------------------------------------------------
    def _pv(self, avail: float, now: float) -> ChargeState:
        """Nur Ueberschuss — mit Ein-/Ausschaltverzoegerung gegen Wolken."""
        c, s = self.cfg, self.state
        # Unterhalb des Geraete-Minimums kann gar nicht geladen werden, ohne
        # Netz zu beziehen. Eine niedriger konfigurierte Einschaltschwelle
        # wuerde sonst starten und sofort wieder verweigern.
        schwelle = max(c.einschalt_w, self.min_w)
        if not s.charging:
            if avail >= schwelle:
                if not s._above:
                    s._above = now
                waited = now - s._above
                if waited >= c.einschalt_delay_s:
                    return self._set(True, self._cap(avail),
                                     f"PV: Ueberschuss {avail:.0f} W")
                return self._set(False, 0,
                                 f"PV: warte {c.einschalt_delay_s - waited:.0f} s "
                                 f"(Ueberschuss {avail:.0f} W)")
            s._above = 0.0
            return self._set(False, 0,
                             f"PV: zu wenig Ueberschuss ({avail:.0f}/{schwelle:.0f} W)")

        # laeuft bereits
        if avail < c.ausschalt_w:
            if not s._below:
                s._below = now
            waited = now - s._below
            if waited >= c.ausschalt_delay_s:
                return self._set(False, 0, f"PV: Ueberschuss weg ({avail:.0f} W)")
            # waehrend der Verzoegerung auf Minimum halten statt abzubrechen
            return self._set(True, self.min_w,
                             f"PV: Minimum, stoppe in {c.ausschalt_delay_s - waited:.0f} s")
        s._below = 0.0
        w = self._cap(avail)
        if w < self.min_w:
            # Ueberschuss ueber Ausschaltschwelle, aber unter Geraete-Minimum
            return self._set(True, self.min_w, f"PV: Geraete-Minimum {self.min_w:.0f} W")
        return self._set(True, w, f"PV: {avail:.0f} W Ueberschuss")

    def _ziel(self, avail: float, now: float) -> ChargeState:
        """Zielladen: PV bevorzugt, Netz nur wenn die Zeit sonst nicht reicht."""
        c = self.cfg
        rest_kwh = c.ziel_kwh - self.state.session_kwh
        if rest_kwh <= 0:
            return self._set(False, 0, "Ziel erreicht")
        secs = _seconds_until(c.ziel_time, now)
        if secs is None:
            return self._set(False, 0, "Zielladen: keine Zielzeit gesetzt")

        # Wie lange braucht es bei voller Leistung? Danach richtet sich,
        # ab wann Netzbezug unvermeidlich ist.
        need_s = (rest_kwh * 3600_000.0) / max(1.0, self.max_w)
        if secs <= need_s:
            return self._set(True, self.max_w,
                             f"Zielladen: Netz noetig, {rest_kwh:.1f} kWh in "
                             f"{secs/60:.0f} min")
        if avail >= self.min_w:
            return self._set(True, self._cap(avail),
                             f"Zielladen: PV {avail:.0f} W, {rest_kwh:.1f} kWh offen")
        return self._set(False, 0,
                         f"Zielladen: warte auf PV ({rest_kwh:.1f} kWh, "
                         f"Puffer {(secs-need_s)/60:.0f} min)")

    # ------------------------------------------------------------------
    def _cap(self, w: float) -> float:
        w = min(w, self.max_w)
        if self.cfg.max_total_w:
            w = min(w, self.cfg.max_total_w)
        return max(0.0, w)

    def _set(self, charging: bool, w: float, reason: str) -> ChargeState:
        s = self.state
        if charging and w < self.min_w:
            charging, w = False, 0.0
            reason += " -> unter Geraete-Minimum"
        if charging != s.charging:
            s.since = self._clock()
            if charging:
                s._below = 0.0
            else:
                s._above = 0.0
        s.charging, s.target_w, s.reason = charging, float(w), reason
        return s


def _seconds_until(hhmm: str, now: float):
    """Sekunden bis zur naechsten Uhrzeit hh:mm (heute oder morgen)."""
    try:
        h, m = (int(x) for x in str(hhmm).split(":"))
    except Exception:
        return None
    lt = time.localtime(now)
    target = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
    if target <= now:
        target += 86400
    return target - now
