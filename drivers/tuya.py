"""Wallbox-Treiber fuer Tuya/SmartLife-Wallboxen — REIN LOKAL (LAN, keine Cloud).

Getestet gegen die Geraeteklasse "Osoeri OS-EC01" (22 kW, Typ 2, WLAN 2,4 GHz,
SmartLife-App). Diese Boxen sprechen kein OCPP und kein Modbus, sondern das
Tuya-LAN-Protokoll auf TCP 6668. Mit device_id + local_key laeuft die Steuerung
vollstaendig im eigenen Netz — die Wallbox darf im Router vom Internet
getrennt werden.

Der local_key wird EINMALIG ausgelesen (siehe tools/tuya_scan.py), danach nie
wieder eine Cloud-Verbindung.

Steuerung:
    set_power(watt) -> Ladestrom in Ampere (6..32 A) ueber den Strom-Datenpunkt
    pause()/resume() -> Lade-Schalter aus/ein

Aufloesung: 1 A Schritt = 230 W (1-phasig) bzw. 690 W (3-phasig). Darunter
kann keine Wallbox regeln — das ist Norm (IEC 61851), kein Treiber-Limit.

Config (config.json -> miners[]):
{
  "type": "tuya",
  "ip": "192.168.1.8",
  "name": "Wallbox Hof",
  "device_id": "bfxxxxxxxxxxxxxxxxxxxx",
  "local_key": "xxxxxxxxxxxxxxxx",
  "protocol": "3.4",
  "phases": 3,
  "volt": 230,
  "min_a": 6,
  "max_a": 32,
  "dp_switch": 1,
  "dp_current": 4,
  "dp_power": 108,
  "dp_state": 14,
  "min_switch_interval_s": 300
}
Die DP-Nummern sind je Geraet unterschiedlich -> mit tools/tuya_scan.py ermitteln.
"""
from __future__ import annotations
import asyncio, base64, contextlib, time
from collections import deque
from .base import MinerDriver, MinerStats


#: Auch eine Handbedienung schaltet nicht schneller als das (Sekunden).
HAND_SPERRE_S = 10

#: Anlaufschutz. Nach dem Einschalten braucht das Fahrzeug Zeit, um mit der Box
#: auszuhandeln (Control Pilot von 9 V auf 6 V). Wird in dieser Zeit wieder
#: abgeschaltet, kommt die Aushandlung nie zustande — die Box gibt frei, das
#: Auto fordert nie an, und es fliesst kein einziges Watt. Genau so gesehen an
#: einer echten Anlage: neun Schaltvorgaenge in zehn Minuten, 0,0 kWh geladen.
#: Die Regelung muss in diesem Fenster warten. Ein Mensch darf trotzdem stoppen.
ANLAUF_S = 180


class TuyaWallbox(MinerDriver):
    #: wird in __init__ aus phases*volt berechnet (1-A-Schritt)
    granularity_w = 230
    stepwise = False

    def __init__(self, ip, name="", device_id="", local_key="", protocol="3.4",
                 phases=3, volt=230, min_a=8, max_a=32,
                 dp_switch=18, dp_current=4, dp_power=9, dp_state=3,
                 dp_temp=24, dp_mode=14, mode_value="charge_now",
                 dp_phase_a=6, dp_phase_b=7, dp_phase_c=8, phase_factor=0.1,
                 dp_fault=10, dp_connection=13,
                 power_factor=1.0, current_factor=1.0,
                 min_switch_interval_s=300, timeout_s=3.0,
                 strom_neustart=False, neustart_pause_s=60,
                 neustart_ab_a=2, neustart_intervall_s=600,
                 neustart_waehrend_ladung=False,
                 schreib_pause_s=0.3, **kw):
        self.phases = max(1, int(phases))
        self.volt = int(volt)
        self.min_a = int(min_a)
        self.max_a = int(max_a)
        self.w_per_a = self.phases * self.volt
        # min_w/max_w ergeben sich aus Ampere x Phasen x Spannung; stehen sie
        # trotzdem in der config, gewinnt die Ampere-Rechnung (Norm-Grenzen).
        kw.pop("min_w", None); kw.pop("max_w", None)
        super().__init__(ip, name,
                         min_w=self.min_a * self.w_per_a,
                         max_w=self.max_a * self.w_per_a, **kw)
        self.granularity_w = self.w_per_a
        self.device_id = device_id
        self.local_key = local_key
        self.protocol = str(protocol)
        self.dp_switch = int(dp_switch)
        self.dp_current = int(dp_current)
        self.dp_power = int(dp_power) if dp_power not in (None, "") else None
        self.dp_state = int(dp_state) if dp_state not in (None, "") else None
        self.dp_temp = int(dp_temp) if dp_temp not in (None, "") else None
        self.dp_mode = int(dp_mode) if dp_mode not in (None, "") else None
        # Strom je Phase (DP6/7/8). Damit laesst sich ablesen, wie viele
        # Phasen wirklich laden — die Box kann nicht umschalten, also
        # entscheidet die Zuleitung, und Raten fuehrt zu falscher
        # Watt-in-Ampere-Umrechnung.
        self.dp_phases = [int(x) for x in (dp_phase_a, dp_phase_b, dp_phase_c)
                          if x not in (None, "")]
        self.phase_factor = float(phase_factor)
        # DP10 Stoerungs-Bitmap und DP13 CP-Zustand. Ohne die sucht man bei
        # einem "Ladefehler" am Auto im Dunkeln.
        self.dp_fault = int(dp_fault) if dp_fault not in (None, "") else None
        self.dp_connection = int(dp_connection) if dp_connection not in (None, "") else None
        self.mode_value = mode_value
        # Rohwert -> Einheit. Beim OS-EC01 liefert DP9 (power_total, scale 3
        # in kW) den Wert bereits in Watt, DP4 den Strom direkt in Ampere.
        self.power_factor = float(power_factor)
        self.current_factor = float(current_factor)
        self.min_switch_interval_s = int(min_switch_interval_s)
        # Manche Boxen uebernehmen einen neuen Ladestrom NUR beim Einschalten —
        # waehrend des Ladens geschriebene Ampere ignorieren sie. Dann hilft nur
        # aus, neuen Wert setzen, kurz warten, wieder ein. Weil das jedes Mal
        # einen Schuetzvorgang und eine Ladepause kostet, ist es abschaltbar und
        # doppelt gebremst: erst ab einer nennenswerten Aenderung, und nicht
        # oefter als `neustart_intervall_s`.
        self.strom_neustart = bool(strom_neustart)
        self.neustart_pause_s = max(5, int(neustart_pause_s))
        self.neustart_ab_a = max(1, int(neustart_ab_a))
        self.neustart_intervall_s = max(0, int(neustart_intervall_s))
        # Darf fuer einen Stromwechsel eine LAUFENDE Ladung unterbrochen
        # werden? Vorgabe nein. Fahrzeuge des VW-Konzerns gehen dabei in einen
        # Ladefehler, der sich nur durch Ab- und Anstecken loesen laesst — an
        # der Anlage genau so passiert. Der neue Strom gilt dann eben ab dem
        # naechsten Einschalten; die Box nimmt ihn ohnehin nur dann an.
        self.neustart_waehrend_ladung = bool(neustart_waehrend_ladung)
        # Mindestabstand zwischen zwei Schreibzugriffen. Tuya-Geraete nehmen
        # schnell aufeinander folgende Befehle ueber dieselbe Verbindung nicht
        # verlaesslich an — sie verwerfen sie oder brechen die Verbindung ab.
        # Beim Einschalten gingen bisher vier Befehle ohne Pause hinaus
        # (Strom, Betriebsart, Strom, Schuetz); die Karte macht genau einen.
        self.schreib_pause_s = max(0.0, float(schreib_pause_s))
        self._letzter_schreib = 0.0
        self._letzte_dps: dict = {}
        # Die letzten Schaltvorgaenge, damit man sie ohne Linux-Konsole ansehen
        # kann. Die Fehlersuche scheiterte bisher daran, dass das Protokoll nur
        # im Journal des Dienstes stand — und wer die Anlage bedient, sitzt vor
        # einem Browser, nicht vor `journalctl`.
        self.schaltungen = deque(maxlen=60)
        self.timeout_s = float(timeout_s)
        self._dev = None
        self._last_switch = 0.0
        self._on = False
        self._an_seit = 0.0           # seit wann eingeschaltet (Anlaufschutz)
        self._neustart_ab = 0.0       # ab wann nach einer Aushandlung wieder ein
        self._aushandlung = False     # laeuft gerade eine Neuaushandlung?
        self._letzte_aushandlung = 0.0
        self._want_off = False        # Aus-Wunsch, der noch aussteht (Taktschutz)
        self._target_a = 0

    # ---------- Verbindung (tinytuya ist blockierend -> in Thread) ----------
    def _connect(self):
        if self._dev is not None:
            return self._dev
        import tinytuya                       # optional, nur wenn Wallbox genutzt
        d = tinytuya.Device(self.device_id, self.ip, self.local_key,
                            version=float(self.protocol))
        d.set_socketTimeout(self.timeout_s)
        d.set_socketPersistent(True)
        self._dev = d
        return d

    def _drop(self):
        try:
            if self._dev:
                self._dev.close()
        except Exception:
            pass
        self._dev = None

    def _status_sync(self) -> dict:
        d = self._connect()
        res = d.status() or {}
        if "Error" in res or "dps" not in res:
            self._drop()
            raise RuntimeError(res.get("Error", "keine dps in Antwort"))
        return res["dps"]

    def _set_sync(self, dp: int, value) -> bool:
        """Einen Datenpunkt schreiben — mit Abstand und einem zweiten Versuch.

        Laeuft im Thread, ein blockierendes `sleep` haelt hier also nichts auf.
        Der Abstand ist noetig, weil das Geraet mehrere Befehle in Folge sonst
        verwirft; der zweite Versuch, weil `_drop()` danach eine frische
        Verbindung aufbaut und der Befehl dann meist durchgeht. Ein verlorener
        Schaltbefehl heisst: Das Auto laedt nicht, und niemand weiss warum.
        """
        letzter = None
        for versuch in (1, 2):
            rest = self.schreib_pause_s - (time.time() - self._letzter_schreib)
            if rest > 0:
                time.sleep(rest)
            try:
                d = self._connect()
                res = d.set_value(dp, value) or {}
                self._letzter_schreib = time.time()
                if isinstance(res, dict) and "Error" in res:
                    raise RuntimeError(res["Error"])
                return True
            except Exception as e:
                letzter = e
                self._letzter_schreib = time.time()
                self._drop()                   # neue Verbindung fuer Versuch 2
        raise RuntimeError(letzter)

    async def _status(self) -> dict:
        return await asyncio.to_thread(self._status_sync)

    async def _set(self, dp: int, value) -> bool:
        """Schreibt einen Datenpunkt. Wirft NICHT — die Regelung faengt bei
        set_power/resume nichts ab, ein unerreichbares Geraet wuerde sonst den
        ganzen Regel-Tick des Standorts abbrechen."""
        try:
            ok = await asyncio.to_thread(self._set_sync, dp, value)
        except Exception as e:
            print(f"[wallbox {self.name}] DP{dp}={value}: {e}")
            return False
        if ok:
            # Was wir eben selbst gesetzt haben, wissen wir — darauf zu warten,
            # dass das Geraet es von sich aus wiederholt, hiesse: Ein gerade
            # eingeschalteter Schuetz gilt bis zur naechsten Meldung als aus,
            # und der naechste Takt schaltet ihn wieder ein.
            #
            # Absichtlich hier und nicht in `_set_sync`: Das ist die
            # Schreiboperation des Treibers, `_set_sync` nur die LAN-Schicht,
            # die in Simulation und Tests ersetzt wird. Buchhaltung gehoert
            # nicht in die Leitung.
            self._letzte_dps[str(dp)] = value
        return ok

    # ---------- Interface ----------
    async def get_stats(self) -> MinerStats:
        st = MinerStats(ip=self.ip, model="Tuya-Wallbox")
        try:
            dps = await self._status()
        except Exception as e:
            st.online = False
            st.state = "offline"
            st.raw = {"error": str(e)}
            return st

        st.online = True
        # Tuya-Antworten sind oft UNVOLLSTAENDIG: Das Geraet schickt nur die
        # Datenpunkte, die sich geaendert haben. Wer jede Antwort fuer das ganze
        # Bild nimmt, liest fehlende Werte als fehlend — und `dps.get("18")`
        # ergibt dann `None`, also „Schuetz aus", obwohl er zu ist.
        #
        # Genau das war an der Anlage zu sehen: Der Einschaltbefehl ging
        # erfolgreich hinaus, und eine Sekunde spaeter stand `Schuetz aus`
        # da — waehrend die Leistung minutenlang auf demselben Wert klebte,
        # weil auch sie nicht mehr mitkam. Die Regelung hat daraufhin ewig
        # versucht einzuschalten, was schon eingeschaltet war.
        #
        # Deshalb wird jede Antwort in ein fortgefuehrtes Bild eingearbeitet,
        # statt es zu ersetzen. Ein Wert, den das Geraet nicht wiederholt, ist
        # unveraendert — nicht verschwunden.
        self._letzte_dps.update(dps)
        neu_gekommen = len(dps)
        dps = dict(self._letzte_dps)
        st.raw = dps
        st.raw["_frisch"] = neu_gekommen
        on = bool(dps.get(str(self.dp_switch), False))
        self._on = on

        amp = _num(dps.get(str(self.dp_current)))
        if amp is not None:
            amp *= self.current_factor

        watt = _num(dps.get(str(self.dp_power))) if self.dp_power else None
        if watt is not None:
            watt *= self.power_factor
        else:
            watt = (amp or 0) * self.w_per_a if on else 0.0

        st.power_w = round(float(watt), 1)
        st.temp_c = _num(dps.get(str(self.dp_temp))) or 0.0 if self.dp_temp else 0.0
        st.state = _state_name(dps.get(str(self.dp_state)) if self.dp_state else None,
                               on, st.power_w)
        st.raw["_amp"] = amp
        st.raw["_plugged"] = _plugged(dps.get(str(self.dp_state)) if self.dp_state else None, on)
        if self.dp_fault is not None:
            st.raw["_faults"] = _stoerungen(dps.get(str(self.dp_fault)))
        if self.dp_connection is not None:
            roh_cp = dps.get(str(self.dp_connection))
            st.raw["_cp"] = roh_cp
            st.raw["_cp_text"] = CP_TEXT.get(str(roh_cp), str(roh_cp or "?"))

        # Strom je Phase und daraus die Zahl der tatsaechlich ladenden Phasen
        stroeme = []
        for dp in self.dp_phases:
            v = _num(dps.get(str(dp)))
            stroeme.append(None if v is None else round(v * self.phase_factor, 1))
        if any(v is not None for v in stroeme):
            st.raw["_phase_a"], st.raw["_phase_b"], st.raw["_phase_c"] = (
                stroeme + [None, None, None])[:3]
            aktiv = sum(1 for v in stroeme if v is not None and v >= 1.0)
            # Nur aussagekraeftig, wenn ueberhaupt geladen wird
            st.raw["_phases_active"] = aktiv if st.power_w > 100 else None

        # Ausstehenden Aus-Wunsch nachholen: die Regelung ruft pause() nur
        # einmal auf — ohne diesen Nachzug bliebe die Box nach einem vom
        # Taktschutz abgelehnten Ausschalten dauerhaft an.
        if self._want_off and on and self._may_switch() and not self.anlauf_rest_s():
            if await self._set(self.dp_switch, False):
                self._on = False
                self._want_off = False
                self._an_seit = 0.0
                self._last_switch = time.time()
                self._protokoll(False, "nachgeholt")
                # Status wurde vor dem Abschalten gelesen — sonst meldeten wir
                # "paused" und gleichzeitig die alte Ladeleistung. 0 W ist hier
                # auch die sichere Richtung: die Regelung verteilt lieber zu
                # wenig als zu viel.
                st.state = "paused"
                st.power_w = 0.0
                st.raw["_amp"] = 0
        elif not on:
            self._want_off = False
        return st

    async def set_power(self, watt: int, grund: str = "") -> bool:
        """Watt-Ziel -> Ladestrom. Unter min_a wird pausiert (Norm-Minimum 6 A)."""
        amp = int(float(watt) // self.w_per_a)     # abrunden: nie mehr ziehen als da ist
        if amp < self.min_a:
            return await self.pause(grund or "unter Mindeststrom")
        amp = min(self.max_a, amp)
        ok = True
        if (self.strom_neustart and self._on and amp != self._target_a
                and self.anlauf_rest_s() <= 0):
            # Diese Box nimmt den Strom nur beim Einschalten an.
            if (abs(amp - self._target_a) < self.neustart_ab_a
                    or self.aushandlung_rest_s() > 0):
                return True             # zu klein oder zu frueh: Strom bleibt
            if self.fahrzeug_da() and not self.neustart_waehrend_ladung:
                # Solange ein Fahrzeug steckt, wird der Schuetz fuer ein paar
                # Ampere nicht geoeffnet: Eine laufende Ladung ginge in den
                # Ladefehler, eine laufende Aushandlung kaeme nie zustande.
                # Der Wunsch bleibt gemerkt und greift beim naechsten
                # Einschalten — dann setzt `resume()` ihn ohnehin.
                self._target_a = amp
                return True
            return await self._aushandeln(amp, grund)
        if amp != self._target_a:
            if not self._on:
                # Sie ist aus: `resume()` setzt den Strom gleich selbst, direkt
                # vor dem Einschalten. Hier zu schreiben waere ein Befehl zu
                # viel — und beim Einschalten zaehlt jeder.
                self._target_a = amp
            else:
                ok = await self._set(self.dp_current, amp)
                if ok:
                    self._target_a = amp
        if not self._on:
            ok = await self.resume(grund) and ok
        return ok

    def _protokoll(self, ein: bool, grund: str, ok: bool = True):
        """Wer schaltet, schreibt es hin — ins Journal UND in den Ringpuffer.

        Ohne das laesst sich hinterher nicht sagen, ob die Regelung, ein Mensch
        oder die Box selbst geschaltet hat, und genau diese Frage kostete bei
        der Fehlersuche die meiste Zeit. Mitgeschrieben wird auch, was das
        Fahrzeug in diesem Moment meldete: Ein Schaltbefehl, der ankommt,
        waehrend der Control Pilot auf 9 V steht, sagt etwas anderes als einer
        bei 6 V.
        """
        was = ("EIN" if ein else "AUS") + ("" if ok else " FEHLGESCHLAGEN")
        print(f"[wallbox {self.name}] Schuetz {was}"
              f"{' — ' + grund if grund else ''}")
        self.schaltungen.append({
            "ts": time.time(), "ein": bool(ein), "ok": bool(ok),
            "grund": grund or "",
            "work_state": self._letzte_dps.get(str(self.dp_state)),
            "cp": (self._letzte_dps.get(str(self.dp_connection))
                   if self.dp_connection else None),
            "amp": _num(self._letzte_dps.get(str(self.dp_current))),
            "watt": _num(self._letzte_dps.get(str(self.dp_power))),
        })

    async def pause(self, grund: str = "") -> bool:
        """Laden beenden. Innerhalb der Schonzeit wird sofort auf den
        Mindeststrom gedrosselt und das Ausschalten nachgeholt, sobald der
        Taktschutz es erlaubt (siehe get_stats)."""
        self._want_off = True
        if not self._on:
            self._want_off = False
            return True
        anlauf = self.anlauf_rest_s()
        if anlauf > 0:
            # Das Fahrzeug handelt gerade aus. Jetzt abzuschalten heisst, dass
            # es nie zu laden beginnt. Drosseln ja, abschalten nein.
            if self._target_a > self.min_a and await self._set(self.dp_current, self.min_a):
                self._target_a = self.min_a
            return True
        if not self._may_switch():
            if self._target_a > self.min_a:          # wenigstens drosseln
                if await self._set(self.dp_current, self.min_a):
                    self._target_a = self.min_a
            return True                              # Aus ist vorgemerkt
        ok = await self._set(self.dp_switch, False)
        if ok:
            self._on = False
            self._want_off = False
            self._an_seit = 0.0
            self._last_switch = time.time()
            self._protokoll(False, grund)
        return ok

    async def resume(self, grund: str = "") -> bool:
        self._want_off = False
        if self._on:
            return True
        if self._neustart_ab:
            if time.time() < self._neustart_ab:
                return False            # das Fahrzeug braucht die Pause
            self._neustart_ab = 0.0
            self._aushandlung = True    # die zwei Schaltvorgaenge gehoeren zusammen
        if not self._may_switch():
            return False
        if (self.dp_mode and self.mode_value
                and self._letzte_dps.get(str(self.dp_mode)) != self.mode_value):
            # sonst kann ein in der App gesetzter Zeitplan / Energie-Modus
            # unseren Ladestrom ueberschreiben. Steht die Betriebsart schon
            # richtig, bleibt der Befehl weg: Jeder Schreibzugriff ist eine
            # Gelegenheit, den entscheidenden danach zu verlieren.
            await self._set(self.dp_mode, self.mode_value)
        if self._target_a < self.min_a:
            self._target_a = self.min_a
        # Den Strom unmittelbar vor dem Einschalten setzen: Boxen, die ihn nur
        # im ausgeschalteten Zustand annehmen, uebernehmen genau jetzt. Steht er
        # schon richtig, bleibt der Befehl weg — vier Befehle ohne Not waren der
        # Grund, warum der entscheidende manchmal verloren ging.
        if _num(self._letzte_dps.get(str(self.dp_current))) != self._target_a:
            await self._set(self.dp_current, self._target_a)
        ok = await self._set(self.dp_switch, True)
        self._aushandlung = False
        if not ok:
            self._protokoll(True, grund, ok=False)
        if ok:
            self._on = True
            self._an_seit = time.time()
            self._last_switch = time.time()
            self._protokoll(True, grund)
        return ok

    def _may_switch(self) -> bool:
        """Ein/Aus-Takten begrenzen — Ladevorgaenge staendig neu zu starten
        moegen weder Fahrzeug noch Schuetz.

        Eine laufende Neuaushandlung ist davon ausgenommen: Sie ist selbst schon
        durch `neustart_intervall_s` gebremst, und ihre zwei Schaltvorgaenge
        gehoeren zusammen. Sonst laege zwischen Aus und Ein der ganze Taktschutz.
        """
        return self._aushandlung or self.sperre_rest_s() <= 0

    def laedt_gerade(self) -> bool:
        """Zieht das Fahrzeug wirklich Strom?"""
        watt = _num(self._letzte_dps.get(str(self.dp_power))) or 0.0
        zustand = str(self._letzte_dps.get(str(self.dp_state)) or "").lower()
        return watt > 200 or "charging" in zustand

    def fahrzeug_da(self) -> bool:
        """Steckt ein Fahrzeug? Dann wird der Schuetz nicht mehr angefasst.

        Anfangs habe ich nur die laufende Ladung geschuetzt. Das war zu wenig:
        Ein Fahrzeug, das gerade **aushandelt** (9 V + PWM, „Verbindung wird
        aufgebaut" im Display), wird von einer Unterbrechung genauso zerstoert —
        es kommt dann gar nicht erst zum Laden. Beides ist derselbe Fall:
        Solange etwas steckt, ist der Ladestrom nicht mehr zu aendern.

        Der neue Wert wird ohnehin beim Einschalten gesetzt (`resume`), und
        eingeschaltet wird nur, wenn der Schuetz aus war — genau dann kostet es
        nichts.
        """
        zustand = str(self._letzte_dps.get(str(self.dp_state)) or "").lower()
        if zustand:
            return "free" not in zustand
        cp = str(self._letzte_dps.get(str(self.dp_connection)) or "").lower()
        return bool(cp) and "12v" not in cp

    def strom_wartet(self) -> bool:
        """Ist ein Stromwunsch offen, den die Box noch nicht hat?

        Verglichen wird der Wunsch mit dem, was im fortgefuehrten Bild steht —
        also mit dem, was das Geraet tatsaechlich kennt. Den Wunsch mit sich
        selbst zu vergleichen ergibt immer „nichts offen".
        """
        if not self.strom_neustart:
            return False
        hat = _num(self._letzte_dps.get(str(self.dp_current)))
        return hat is not None and int(hat) != int(self._target_a)

    def aushandlung_rest_s(self) -> float:
        """Wann der Ladestrom fruehestens wieder geaendert werden kann."""
        if not self.strom_neustart or not self._letzte_aushandlung:
            return 0.0
        rest = self.neustart_intervall_s - (time.time() - self._letzte_aushandlung)
        return max(0.0, rest)

    async def _aushandeln(self, amp: int, grund: str) -> bool:
        """Ladestrom ueber einen Neustart aendern: aus, neuer Wert, spaeter ein.

        **Die Reihenfolge ist entscheidend.** Diese Box nimmt einen neuen
        Ladestrom nur an, wenn sie AUS ist — ein Schreibzugriff im Betrieb
        verpufft. Also zuerst der Schuetz, dann der Wert. Andersherum aendert
        sich gar nichts, und der Ladevorgang waere fuer nichts unterbrochen
        worden.

        Das Wiedereinschalten passiert NICHT hier, sondern beim naechsten Takt
        ueber `resume()` — der Treiber darf den Regelkreis nicht eine Minute
        lang blockieren. `_neustart_ab` haelt so lange die Tuer zu.
        """
        self._aushandlung = True
        try:
            if not await self._set(self.dp_switch, False):
                return False
            await self._set(self.dp_current, amp)    # jetzt, wo sie aus ist
            self._target_a = amp
            self._on = False
            self._an_seit = 0.0
            self._last_switch = time.time()
            self._letzte_aushandlung = time.time()
            self._neustart_ab = time.time() + self.neustart_pause_s
            self._protokoll(False, f"Neuaushandlung auf {amp} A — {grund}")
            return True
        finally:
            self._aushandlung = False

    def sperre_rest_s(self) -> float:
        """Wie lange der Taktschutz noch sperrt (0 = frei).

        Gehoert nach aussen sichtbar: Solange hier etwas steht, kann die
        Oberflaeche "Sofortladen 16 A" anzeigen, waehrend die Box nie einen
        Befehl bekommen hat. Eine Absicht, die niemand ausfuehrt, muss man
        sehen koennen.
        """
        if not self._last_switch:
            return 0.0
        rest = self.min_switch_interval_s - (time.time() - self._last_switch)
        return max(0.0, rest)

    def anlauf_rest_s(self) -> float:
        """Wie lange das Fahrzeug noch ungestoert aushandeln darf (0 = frei)."""
        if not self._an_seit:
            return 0.0
        return max(0.0, ANLAUF_S - (time.time() - self._an_seit))

    def takt_freigeben(self):
        """Den Taktschutz verkuerzen — jemand hat es von Hand verlangt.

        Der Schutz ist gegen die **Regelung** gedacht, die bei jeder Wolke
        schalten wuerde. Ein Mensch, der auf „Sofort" drueckt, taktet nicht;
        fuer ihn war die Sperre nur ein Knopf ohne Wirkung. Genau das war der
        Fehler: Start in der Oberflaeche, nichts passiert, und dazu keine
        Meldung.

        Ganz aufgehoben wird sie trotzdem nicht. Wer zwischen zwei Modi hin und
        her drueckt, wuerde den Schuetz sonst im Sekundentakt klappern lassen —
        und das ist genau das, wogegen der Schutz da ist. `HAND_SPERRE_S` ist
        die Untergrenze, die auch von Hand gilt. Nach dem Schalten laeuft die
        volle Sperre wieder.
        """
        rest = min(self.sperre_rest_s(), HAND_SPERRE_S)
        self._last_switch = time.time() - (self.min_switch_interval_s - rest)
        self._an_seit = 0.0          # wer von Hand stoppt, meint es auch so


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


#: DP10 ist ein Bitmap; Reihenfolge laut Geraetemodell
FAULT_BITS = (
    ("ov_cr", "Ueberstrom"),
    ("ov2_cr_fault", "Ueberstrom 2"),
    ("ov_vol", "Ueberspannung"),
    ("undervoltage_alarm", "Unterspannung"),
    ("contactor_adhesion", "Schuetz klebt"),
    ("contactor_fault", "Schuetz-Stoerung"),
    ("earth_fault", "Erdungsfehler"),
    ("meter_hardware_alarm", "Zaehler-Hardware"),
    ("scram_fault", "Not-Aus"),
    ("cp_fault", "CP-Signal-Stoerung"),
    ("meter_commu_fault", "Zaehler-Kommunikation"),
    ("card_reader_fault", "Kartenleser"),
    ("cir_short_fault", "Kurzschluss"),
    ("adhesion_fault", "Verklebung"),
    ("self_test_alarm", "Selbsttest"),
    ("leakagecurr_alarm", "Fehlerstrom"),
)

#: DP13 — Spannung am Control Pilot verraet, was das Fahrzeug tut
CP_TEXT = {
    "controlpi_12v": "12 V — kein Fahrzeug",
    "controlpi_12v_pwm": "12 V + PWM — kein Fahrzeug, Box bereit",
    "controlpi_9v": "9 V — Fahrzeug steckt, Box gibt nicht frei",
    "controlpi_9v_pwm": "9 V + PWM — Fahrzeug steckt, Box gibt frei, Auto fordert nicht an",
    "controlpi_6v": "6 V — Fahrzeug fordert an, Box gibt nicht frei",
    "controlpi_6v_pwm": "6 V + PWM — laedt",
    "controlpi_error": "CP-Fehler",
}


def _stoerungen(roh) -> list:
    """Bitmap in Klartext. Leere Liste heisst: keine Stoerung.

    Tuya liefert Bitmap-Datenpunkte meist als Zahl, manche Geraete und
    Firmware-Staende aber base64-kodiert (`"IA=="` ist 0x20). Unuebersetzt ist
    eine Stoerungsmeldung nichts wert — wer im Fehlerfall vor „IA==" sitzt,
    muss von Hand dekodieren, statt „Schuetz klebt" zu lesen.
    """
    if roh in (None, "", 0, "0"):
        return []
    wert = None
    try:
        wert = int(roh)
    except (TypeError, ValueError):
        with contextlib.suppress(Exception):
            wert = int.from_bytes(base64.b64decode(str(roh), validate=True), "big")
    if wert is None:
        return [f"unbekannte Meldung ({roh})"]
    if wert == 0:
        return []
    treffer = [text for i, (_, text) in enumerate(FAULT_BITS) if wert & (1 << i)]
    # Ein gesetztes Bit, das das Geraetemodell nicht kennt, darf nicht
    # verschwinden: eine verschwiegene Stoerung ist schlimmer als eine rohe.
    rest = wert & ~((1 << len(FAULT_BITS)) - 1)
    if rest:
        treffer.append(f"unbekanntes Bit (0x{rest:x})")
    return treffer


def _plugged(raw_state, on: bool) -> bool:
    """Steckt ein Fahrzeug? charger_free heisst: Dose frei. Ohne work_state
    laesst es sich nicht sicher sagen — dann nehmen wir "ja" an, sonst wuerde
    die Regelung nie starten."""
    s = str(raw_state).lower() if raw_state is not None else ""
    if not s:
        return True
    return "free" not in s


def _state_name(raw_state, on: bool, watt: float) -> str:
    """work_state (DP3) auf die Begriffe der Regelung abbilden.

    Enum des OS-EC01: charger_free, charger_insert, charger_free_fault,
    charger_wait, charger_charging, charger_pause, charger_end, charger_fault.
    """
    s = str(raw_state).lower() if raw_state is not None else ""
    if "fault" in s:
        return "error"
    if "charging" in s:
        return "mining"
    if s.startswith("charger_"):          # insert / wait / pause / end / free
        return "paused"
    return "mining" if (on and watt > 100) else "paused"
