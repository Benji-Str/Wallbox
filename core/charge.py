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
  zeit    — laedt in festgelegten Zeitfenstern mit festem Strom, unabhaengig
            von der Sonne (Nachttarif). Braucht keinen Zaehler.

Die Regelung rechnet in Watt. Die Umrechnung auf Ampere macht der Treiber,
der die Geraetegrenzen kennt (beim OS-EC01 8-32 A).
"""
from __future__ import annotations
import time
from dataclasses import dataclass, field

MODES = ("stop", "sofort", "pv", "minpv", "ziel", "zeit")


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
    # -- Zeitladen: Liste von Fenstern --
    # [{"aktiv":true,"von":"22:00","bis":"06:00","tage":[0,1,2,3,4],"strom_a":16}]
    # tage: 0 = Montag ... 6 = Sonntag; bezogen auf den BEGINN des Fensters
    zeit_plaene: list = field(default_factory=list)
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
             plugged: bool = True, session_kwh: float = 0.0,
             meter_ok: bool = True) -> ChargeState:
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

        # Ohne Zaehlerwerte laesst sich kein Ueberschuss rechnen. Das ist
        # etwas anderes als "keine Sonne" und muss auch so heissen, sonst
        # sucht man den Fehler an der falschen Stelle. Sofortladen und
        # Zeitladen brauchen den Zaehler nicht und laufen weiter — wer nach
        # Tarif laedt, will laden, auch wenn der Zaehler ausfaellt.
        if not meter_ok and self.cfg.mode not in ("sofort", "zeit"):
            return self._set(False, 0, "Zaehlerwerte fehlen — Modus braucht sie")

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

        if self.cfg.mode == "zeit":
            return self._zeit(now)

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

    def _zeit(self, now: float) -> ChargeState:
        """Feste Zeitfenster — wie Sofortladen, nur eben nur dann.

        Braucht keinen Zaehler: wer nach Tarif laedt, will laden, auch wenn
        gerade keine Sonne da ist.
        """
        plan = naechstes_fenster(self.cfg.zeit_plaene, now)
        if not plan:
            naechster = fenster_beginn(self.cfg.zeit_plaene, now)
            return self._set(False, 0,
                             f"Zeitladen: ausserhalb der Fenster"
                             + (f", naechstes {naechster}" if naechster else
                                " (keine Fenster gesetzt)"))
        a = int(plan.get("strom_a") or self.cfg.min_a)
        w = self._cap(max(self.min_w, a * self.w_per_a))
        return self._set(True, w,
                         f"Zeitladen {plan.get('von')}–{plan.get('bis')} "
                         f"mit {a} A")

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


# ---------------------------------------------------------------- Zeitfenster
def _minuten(hhmm: str):
    try:
        h, m = (int(x) for x in str(hhmm).split(":"))
        return h * 60 + m
    except Exception:
        return None


def im_fenster(plan: dict, now: float) -> bool:
    """Liegt `now` in diesem Fenster?

    Der Fall ueber Mitternacht ist der wichtige: 22:00-06:00 gehoert zum
    STARTTAG. Wer "Mo-Fr, 22:00-06:00" einstellt, meint die Nacht von Freitag
    auf Samstag mit — aber nicht die von Sonntag auf Montag.
    """
    if not plan.get("aktiv", True):
        return False
    von, bis = _minuten(plan.get("von")), _minuten(plan.get("bis"))
    if von is None or bis is None or von == bis:
        return False
    lt = time.localtime(now)
    jetzt = lt.tm_hour * 60 + lt.tm_min
    tage = plan.get("tage")
    tage = list(range(7)) if not tage else [int(t) for t in tage]

    if von < bis:                      # normales Fenster am selben Tag
        return lt.tm_wday in tage and von <= jetzt < bis
    if jetzt >= von:                   # Abendteil — Starttag ist heute
        return lt.tm_wday in tage
    if jetzt < bis:                    # Morgenteil — Starttag war gestern
        return (lt.tm_wday - 1) % 7 in tage
    return False


def naechstes_fenster(plaene, now: float):
    for p in plaene or []:
        if im_fenster(p, now):
            return p
    return None


def fenster_beginn(plaene, now: float) -> str:
    """Naechster Beginn als "Di 22:00" — nur fuer die Anzeige."""
    NAMEN = ("Mo", "Di", "Mi", "Do", "Fr", "Sa", "So")
    lt = time.localtime(now)
    jetzt = lt.tm_hour * 60 + lt.tm_min
    besten = None
    for p in plaene or []:
        if not p.get("aktiv", True):
            continue
        von = _minuten(p.get("von"))
        if von is None:
            continue
        tage = p.get("tage") or list(range(7))
        for d in range(8):             # heute bis in eine Woche
            tag = (lt.tm_wday + d) % 7
            if int(tag) not in [int(t) for t in tage]:
                continue
            wartet = d * 1440 + von - jetzt
            if wartet < 0:
                continue
            if besten is None or wartet < besten[0]:
                besten = (wartet, f"{NAMEN[tag]} {p.get('von')}")
    return besten[1] if besten else ""
