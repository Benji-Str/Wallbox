"""Simulierte Wallbox — zum Testen ohne Hardware.

Erbt vom echten Tuya-Treiber und ersetzt nur die LAN-Schicht durch eine
Simulation. Dadurch wird genau der Code getestet, der spaeter auch am Geraet
laeuft: Ampere-Umrechnung, Taktschutz, Zustandsabbildung, Steckererkennung.

Verhaelt sich wie die OS-EC01: 8-32 A, dreiphasig, Leistung faehrt traege
nach, Energiezaehler laeuft mit, Temperatur steigt mit Last.
"""
from __future__ import annotations
import time
from .tuya import TuyaWallbox


class MockWallbox(TuyaWallbox):
    def __init__(self, ip="mock", name="Wallbox (Simulation)",
                 plugged=True, full_kwh=0.0, **kw):
        kw.setdefault("device_id", "mock")
        kw.setdefault("local_key", "mock")
        super().__init__(ip, name, **kw)
        self._plugged = bool(plugged)
        self._full_kwh = float(full_kwh)     # 0 = Auto wird nie voll
        self._cur_w = 0.0
        self._amp = self.min_a
        self._sw = False
        self._total_kwh = 412.7
        self._sess_kwh = 0.0
        self._t = time.time()

    # -- statt Netzwerk: rechnen --
    def _status_sync(self) -> dict:
        now = time.time()
        dt = max(0.0, now - self._t)
        self._t = now

        goal = self._amp * self.w_per_a if (self._sw and self._plugged) else 0.0
        self._cur_w += (goal - self._cur_w) * min(1.0, dt / 4.0)   # traege Annaeherung
        if self._cur_w < 50:
            self._cur_w = 0.0

        kwh = self._cur_w * dt / 3_600_000.0
        self._total_kwh += kwh
        self._sess_kwh += kwh

        if self._full_kwh and self._sess_kwh >= self._full_kwh:
            state = "charger_end"            # Auto voll
            self._cur_w = 0.0
        elif not self._plugged:
            state = "charger_free"
            self._sess_kwh = 0.0
        elif self._cur_w > 50:
            state = "charger_charging"
        else:
            state = "charger_insert"

        return {
            str(self.dp_switch): self._sw,
            str(self.dp_current): self._amp,
            str(self.dp_power): round(self._cur_w),
            str(self.dp_state): state,
            str(self.dp_temp): round(22 + 28 * self._cur_w / self.max_w),
            str(self.dp_mode): "charge_now",
            "1": round(self._total_kwh * 100),
            "25": round(self._sess_kwh * 100),
        }

    def _set_sync(self, dp: int, value) -> bool:
        if dp == self.dp_current:
            self._amp = int(value)
        elif dp == self.dp_switch:
            self._sw = bool(value)
        return True

    # -- zum Durchspielen im Test --
    def plug(self, yes: bool = True):
        self._plugged = yes
        if not yes:
            self._sess_kwh = 0.0
