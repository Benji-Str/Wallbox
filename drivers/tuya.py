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
import asyncio, time
from .base import MinerDriver, MinerStats


class TuyaWallbox(MinerDriver):
    #: wird in __init__ aus phases*volt berechnet (1-A-Schritt)
    granularity_w = 230
    stepwise = False

    def __init__(self, ip, name="", device_id="", local_key="", protocol="3.4",
                 phases=3, volt=230, min_a=8, max_a=32,
                 dp_switch=18, dp_current=4, dp_power=9, dp_state=3,
                 dp_temp=24, dp_mode=14, mode_value="charge_now",
                 power_factor=1.0, current_factor=1.0,
                 min_switch_interval_s=300, timeout_s=3.0, **kw):
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
        self.mode_value = mode_value
        # Rohwert -> Einheit. Beim OS-EC01 liefert DP9 (power_total, scale 3
        # in kW) den Wert bereits in Watt, DP4 den Strom direkt in Ampere.
        self.power_factor = float(power_factor)
        self.current_factor = float(current_factor)
        self.min_switch_interval_s = int(min_switch_interval_s)
        self.timeout_s = float(timeout_s)
        self._dev = None
        self._last_switch = 0.0
        self._on = False
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
        d = self._connect()
        res = d.set_value(dp, value) or {}
        if isinstance(res, dict) and "Error" in res:
            self._drop()
            raise RuntimeError(res["Error"])
        return True

    async def _status(self) -> dict:
        return await asyncio.to_thread(self._status_sync)

    async def _set(self, dp: int, value) -> bool:
        """Schreibt einen Datenpunkt. Wirft NICHT — die Regelung faengt bei
        set_power/resume nichts ab, ein unerreichbares Geraet wuerde sonst den
        ganzen Regel-Tick des Standorts abbrechen."""
        try:
            return await asyncio.to_thread(self._set_sync, dp, value)
        except Exception as e:
            print(f"[wallbox {self.name}] DP{dp}={value}: {e}")
            return False

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
        st.raw = dps
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

        # Ausstehenden Aus-Wunsch nachholen: die Regelung ruft pause() nur
        # einmal auf — ohne diesen Nachzug bliebe die Box nach einem vom
        # Taktschutz abgelehnten Ausschalten dauerhaft an.
        if self._want_off and on and self._may_switch():
            if await self._set(self.dp_switch, False):
                self._on = False
                self._want_off = False
                self._last_switch = time.time()
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

    async def set_power(self, watt: int) -> bool:
        """Watt-Ziel -> Ladestrom. Unter min_a wird pausiert (Norm-Minimum 6 A)."""
        amp = int(float(watt) // self.w_per_a)     # abrunden: nie mehr ziehen als da ist
        if amp < self.min_a:
            return await self.pause()
        amp = min(self.max_a, amp)
        ok = True
        if amp != self._target_a:
            ok = await self._set(self.dp_current, amp)
            if ok:
                self._target_a = amp
        if not self._on:
            ok = await self.resume() and ok
        return ok

    async def pause(self) -> bool:
        """Laden beenden. Innerhalb der Schonzeit wird sofort auf den
        Mindeststrom gedrosselt und das Ausschalten nachgeholt, sobald der
        Taktschutz es erlaubt (siehe get_stats)."""
        self._want_off = True
        if not self._on:
            self._want_off = False
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
            self._last_switch = time.time()
        return ok

    async def resume(self) -> bool:
        self._want_off = False
        if self._on:
            return True
        if not self._may_switch():
            return False
        if self.dp_mode and self.mode_value:
            # sonst kann ein in der App gesetzter Zeitplan / Energie-Modus
            # unseren Ladestrom ueberschreiben
            await self._set(self.dp_mode, self.mode_value)
        if self._target_a < self.min_a:
            self._target_a = self.min_a
            await self._set(self.dp_current, self.min_a)
        ok = await self._set(self.dp_switch, True)
        if ok:
            self._on = True
            self._last_switch = time.time()
        return ok

    def _may_switch(self) -> bool:
        """Ein/Aus-Takten begrenzen — Ladevorgaenge staendig neu zu starten
        moegen weder Fahrzeug noch Schuetz."""
        return (time.time() - self._last_switch) >= self.min_switch_interval_s


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


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
