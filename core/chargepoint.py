"""Ein Ladepunkt = Wallbox-Treiber + Lademodus-Regelung + Ladelog.

Bindeglied zwischen core/charge.py (entscheidet: wieviel Watt) und dem
Treiber (setzt Ampere). Zaehlt ausserdem Ladevorgaenge mit, damit hinterher
nachvollziehbar ist, wieviel aus PV und wieviel aus dem Netz kam.
"""
from __future__ import annotations
import time
from core.charge import ChargeController, ChargeConfig, MODES
from meter.mid import MidMeter, MidReading

_DRIVERS = None


def _drivers():
    global _DRIVERS
    if _DRIVERS is None:
        from drivers.tuya import TuyaWallbox
        from drivers.mock import MockWallbox
        _DRIVERS = {"tuya": TuyaWallbox, "wallbox_tuya": TuyaWallbox,
                    "mock": MockWallbox, "wallbox_mock": MockWallbox}
    return _DRIVERS


class ChargePoint:
    def __init__(self, cfg: dict, log=print):
        self.cfg = cfg
        self.id = cfg.get("id") or cfg.get("ip", "cp1")
        self.name = cfg.get("name") or self.id
        self.log = log
        drv_cfg = {k: v for k, v in cfg.items()
                   if not str(k).startswith("_")
                   and k not in ("id", "type", "charge", "mid_meter")}
        cls = _drivers().get(cfg.get("type", "tuya"), _drivers()["tuya"])
        self.driver = cls(**drv_cfg)
        self.ctrl = ChargeController(ChargeConfig(**(cfg.get("charge") or {})),
                                     self.driver.min_w, self.driver.max_w,
                                     self.driver.w_per_a)
        # Optionaler MID-Zaehler im Abgang der Wallbox. Ist er da, gilt SEIN
        # Zaehlerstand fuer das Ladelog — der interne Zaehler der Box ist ein
        # Betriebswert ohne Beglaubigung.
        mm = cfg.get("mid_meter")
        self.mid = MidMeter(**{k: v for k, v in mm.items()
                               if not str(k).startswith("_")}) if mm else None
        if self.mid and self.mid.mode == "mock":
            # der simulierte Zaehler misst, was die Box gerade zieht
            self.mid.power_cb = lambda: self.stats.power_w if self.stats else 0.0
        self.mid_reading = MidReading()
        self.stats = None
        self.state = None
        self._total_kwh = None      # letzter bekannter Zaehlerstand der Box
        self._stumm_seit = 0.0      # seit wann laden gewollt, Schuetz aber aus
        self.session = None          # laufender Ladevorgang
        self.allocated_w = 0.0       # was dieser Ladepunkt diesen Takt belegt
        # Erkennung "Fahrzeug ist voll": Die Box gibt Strom frei, das Auto
        # nimmt aber keinen mehr. Das ist der einzige Zeitpunkt, an dem sich
        # ueber die Akkugroesse ueberhaupt etwas sagen laesst.
        self.voll_w = float(cfg.get("voll_w", 200))
        self.voll_delay_s = float(cfg.get("voll_delay_s", 300))
        self._leerlauf_seit = 0.0
        self._voll = False

    # ------------------------------------------------------------------
    async def tick(self, feed_in_w: float, meter_ok: bool = True,
                   preis_ct: float | None = None,
                   guenstige_stunde: bool = False) -> float:
        """feed_in_w: aktuelle Netz-Einspeisung. Rueckgabe: belegte Watt.

        meter_ok=False heisst: der Zaehler liefert nichts. Die PV-Modi
        pausieren dann mit klarer Begruendung statt auf 0 W Ueberschuss zu
        schliessen; Sofortladen braucht den Zaehler nicht."""
        self.stats = st = await self.driver.get_stats()
        # Wollte der letzte Takt laden, und der Schuetz ist immer noch aus?
        # Dann kommt der Befehl nicht an. Zwei Ursachen sind bekannt: der
        # Taktschutz sperrt noch, oder an der Box ist die Kartenpflicht aktiv —
        # dann laedt sie nur nach Vorhalten der Karte und ignoriert jede
        # Freigabe uebers Netz. Gemessen wird die **Dauer**, nicht der
        # Augenblick: Unmittelbar nach dem Einschalten ist der Zustand noch vom
        # Lesen vor dem Schreiben, das waere ein Fehlalarm bei jedem Start.
        if bool(self.state and self.state.charging) and not bool(
                st.raw.get(str(self.driver.dp_switch))):
            self._stumm_seit = self._stumm_seit or time.time()
        else:
            self._stumm_seit = 0.0
        if self.mid:
            self.mid_reading = await self.mid.read()
        if not st.online:
            self.allocated_w = 0.0
            return 0.0

        # Der Ueberschuss muss enthalten, was die Box gerade schon zieht —
        # sonst wuerde sie sich bei jedem Takt selbst wegregeln.
        surplus = feed_in_w + st.power_w
        plugged = bool(st.raw.get("_plugged", True))
        session_kwh = self._session_kwh(st)

        roh = st.raw.get("1")
        if roh not in (None, ""):
            try:
                self._total_kwh = round(float(roh) / 100.0, 2)
            except (TypeError, ValueError):
                pass

        self.state = s = self.ctrl.tick(surplus, st.power_w, plugged,
                                        session_kwh, meter_ok,
                                        preis_ct, guenstige_stunde)
        self._pruefe_voll(st, s, plugged)
        self._track_session(st, s, plugged)

        if s.charging:
            # Der Grund geht mit: Nur so steht im Protokoll, WARUM geschaltet
            # wurde, und nicht bloss dass etwas geschaltet wurde.
            await self.driver.set_power(int(s.target_w), f"{s.mode}: {s.reason}")
            self.allocated_w = s.target_w
        else:
            await self.driver.pause(f"{s.mode}: {s.reason}")
            self.allocated_w = 0.0
        return self.allocated_w

    # ------------------------------------------------------------------
    def _session_kwh(self, st) -> float:
        """Energie des laufenden Ladevorgangs. Mit MID-Zaehler aus dessen
        Zaehlerstand-Differenz, sonst aus DP25 (0,01 kWh) der Box."""
        if self.mid and self.mid_reading.ok and self.session:
            start = self.session.get("meter_start")
            if start is not None:
                return max(0.0, self.mid_reading.energy_kwh - start)
        raw = st.raw.get("25")
        try:
            return float(raw) / 100.0
        except (TypeError, ValueError):
            return 0.0

    def _pruefe_voll(self, st, s, plugged: bool):
        """Steht Strom bereit und das Auto nimmt keinen — dann ist es voll.

        Bewusst mit Verzoegerung: Fahrzeuge machen beim Ladestart und beim
        Ausbalancieren der Zellen kurze Pausen. Wer die als "voll" wertet,
        lernt eine viel zu kleine Akkugroesse.

        Vorsicht bei der Deutung: Das Auto kann auch aus eigenem Antrieb
        aufhoeren — eigene Ladegrenze, eigener Abfahrtszeitplan. Deshalb
        heisst das Ergebnis "am Stueck angenommene Energie" und nie
        "so gross ist der Akku".
        """
        if not plugged:
            self._leerlauf_seit, self._voll = 0.0, False
            return
        if not s.charging or st.power_w > self.voll_w:
            self._leerlauf_seit, self._voll = 0.0, False
            return
        if not self._leerlauf_seit:
            self._leerlauf_seit = time.time()
        elif time.time() - self._leerlauf_seit >= self.voll_delay_s:
            self._voll = True

    def _track_session(self, st, s, plugged: bool):
        if s.charging and not self.session:
            self.session = {"start": time.time(), "mode": s.mode,
                            "kwh_start": self._session_kwh(st), "peak_w": 0.0}
            if self.mid and self.mid_reading.ok:
                # Zaehlerstand bei Ladebeginn — das ist der Wert, der fuer eine
                # Abrechnung zaehlt, nicht die Differenz allein.
                self.session["meter_start"] = self.mid_reading.energy_kwh
        if self.session:
            self.session["peak_w"] = max(self.session["peak_w"], st.power_w)
            if s.charging:
                # nur waehrend des Ladens nachfuehren — sonst stuende am Ende
                # der Modus im Log, der den Vorgang BEENDET hat (z. B. "stop")
                self.session["mode"] = s.mode
        if self.session and (not s.charging or not plugged or self._voll):
            self.session["end"] = time.time()
            # Warum der Vorgang endete — nur "voll" taugt zum Lernen.
            self.session["ende"] = ("voll" if self._voll and plugged
                                    else "abgesteckt" if not plugged
                                    else "beendet")
            if self.mid and self.mid_reading.ok and "meter_start" in self.session:
                self.session["meter_end"] = self.mid_reading.energy_kwh
                self.session["kwh"] = round(
                    max(0.0, self.session["meter_end"] - self.session["meter_start"]), 3)
                self.session["source"] = "mid"
            else:
                self.session["kwh"] = round(
                    max(0.0, self._session_kwh(st) - self.session["kwh_start"]), 2)
                self.session["source"] = "wallbox"
            self.session["minutes"] = round(
                (self.session["end"] - self.session["start"]) / 60.0, 1)
            done, self.session = self.session, None
            if done["kwh"] > 0 or done["minutes"] >= 1:
                from core.chargelog import append_session
                append_session(self.id, done)
                self.log(f"[cp {self.id}] Ladevorgang beendet: "
                         f"{done['kwh']} kWh in {done['minutes']} min")

    # ------------------------------------------------------------------
    def set_mode(self, mode: str, **kw) -> bool:
        if mode not in MODES:
            return False
        # Ein Moduswechsel kommt immer von einem Menschen an der Oberflaeche.
        # Der Taktschutz ist gegen die Regelung gedacht, nicht gegen ihn —
        # sonst drueckt er auf „Sofort" und bis zu fuenf Minuten lang
        # passiert nichts, ohne dass irgendwo steht warum.
        if mode != self.ctrl.cfg.mode:
            self.driver.takt_freigeben()
        self.ctrl.cfg.mode = mode
        for k, v in kw.items():
            if v is None or not hasattr(self.ctrl.cfg, k):
                continue
            alt = getattr(self.ctrl.cfg, k)
            # Listen (Zeitplaene) nicht durch den Typ des Altwerts zwingen —
            # list(dict) wuerde die Plaene in ihre Schluessel verwandeln.
            setattr(self.ctrl.cfg, k, v if isinstance(alt, list) else type(alt)(v))
        self.cfg.setdefault("charge", {})
        self.cfg["charge"].update({"mode": mode, **{k: v for k, v in kw.items() if v is not None}})
        return True

    def live(self) -> dict:
        st, s, c = self.stats, self.state, self.ctrl.cfg
        return {
            "id": self.id, "name": self.name,
            "online": bool(st and st.online),
            "plugged": bool(st and st.raw.get("_plugged")),
            "state": st.state if st else "?",
            "power_w": round(st.power_w, 0) if st else 0,
            "amp": st.raw.get("_amp") if st else None,
            "temp_c": round(st.temp_c, 0) if st else 0,
            "faults": (st.raw.get("_faults") or []) if st else [],
            "cp": st.raw.get("_cp") if st else None,
            "cp_text": st.raw.get("_cp_text") if st else None,
            "switch_on": bool(st.raw.get(str(self.driver.dp_switch))) if st else False,
            # Solange hier etwas steht, kommt kein Schaltbefehl an der Box an.
            "sperre_s": round(self.driver.sperre_rest_s()),
            # So lange darf das Fahrzeug noch ungestoert aushandeln.
            "anlauf_s": round(getattr(self.driver, "anlauf_rest_s", lambda: 0.0)()),
            # Boxen, die den Strom nur beim Einschalten annehmen: wann er
            # fruehestens wieder geaendert werden kann (0 = jederzeit).
            "strom_neustart": bool(getattr(self.driver, "strom_neustart", False)),
            "stromwechsel_s": round(
                getattr(self.driver, "aushandlung_rest_s", lambda: 0.0)()),
            # Die letzten Schaltvorgaenge — sichtbar ohne Linux-Konsole.
            "schaltungen": list(getattr(self.driver, "schaltungen", []))[-20:],
            # So lange will die Steuerung schon laden, ohne dass der Schuetz
            # zugeht. 0 = alles in Ordnung.
            "nicht_geschaltet_s": (round(time.time() - self._stumm_seit)
                                   if self._stumm_seit else 0),
            "phases_cfg": self.driver.phases,
            "phases_active": st.raw.get("_phases_active") if st else None,
            "phase_a": st.raw.get("_phase_a") if st else None,
            "phase_b": st.raw.get("_phase_b") if st else None,
            "phase_c": st.raw.get("_phase_c") if st else None,
            "session_kwh": round(self._session_kwh(st), 2) if st else 0,
            # DP1 (Zaehlerstand) liefert die Box lokal nicht in jeder
            # Statusantwort. Dann den letzten bekannten Wert zeigen statt 0 —
            # ein Zaehlerstand, der auf null springt, ist schlimmer als keiner.
            "total_kwh": self._total_kwh,
            "mode": c.mode,
            "target_w": round(s.target_w, 0) if s else 0,
            "charging": bool(s and s.charging),
            "reason": s.reason if s else "–",
            "min_w": self.driver.min_w, "max_w": self.driver.max_w,
            "w_per_a": self.driver.w_per_a,
            "min_a": self.driver.min_a, "max_a": self.driver.max_a,
            "mid": (None if not self.mid else {
                "ok": self.mid_reading.ok, "energy_kwh": self.mid_reading.energy_kwh,
                "power_w": self.mid_reading.power_w, "error": self.mid_reading.error}),
            "cfg": {"sofort_a": c.sofort_a, "min_a": c.min_a,
                    "eco_max_ct": c.eco_max_ct, "eco_a": c.eco_a,
                    "eco_stunden": c.eco_stunden,
                    "zeit_plaene": c.zeit_plaene,
                    "einschalt_w": c.einschalt_w, "einschalt_delay_s": c.einschalt_delay_s,
                    "ausschalt_w": c.ausschalt_w, "ausschalt_delay_s": c.ausschalt_delay_s,
                    "ziel_kwh": c.ziel_kwh, "ziel_time": c.ziel_time,
                    "max_total_w": c.max_total_w},
        }
