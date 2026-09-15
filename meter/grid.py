"""mecMeter-Leser (MEC electronics) — liefert Netz/Einspeisung.

Zwei Betriebsarten:
  - modbus : Modbus TCP (Port 502) — robust, bevorzugt. Register aus der
             Anleitung eintragen (REG_* unten), sobald der Meter da ist.
  - json   : REST/JSON (der mecMeter kann XML/JSON) — Fallback.
  - mock   : simuliert Einspeisung/Bezug fuer Tests ohne Hardware.

Vorzeichen-Konvention im ganzen Projekt:
    grid_w > 0  = Netzbezug (Import)
    grid_w < 0  = Einspeisung (Export)   ->  feed_in_w = max(0, -grid_w)
"""
from __future__ import annotations
import asyncio, math, time, random
from dataclasses import dataclass


@dataclass
class MeterReading:
    ok: bool = False
    grid_w: float = 0.0          # + Bezug / - Einspeisung
    pv_w: float = 0.0            # PV-Erzeugung (aus Wechselrichter/Fronius; 0 wenn unbekannt)
    l1_w: float = 0.0
    l2_w: float = 0.0
    l3_w: float = 0.0
    ts: float = 0.0

    @property
    def feed_in_w(self) -> float:
        return max(0.0, -self.grid_w)

    @property
    def import_w(self) -> float:
        return max(0.0, self.grid_w)


class MecMeter:
    # ── Modbus-Register (PLATZHALTER — aus mecMeter-Anleitung eintragen) ──
    # Beispielhafte Namen; echte Adressen/Skalierung beim Geraet mappen.
    REG_P_TOTAL = 0      # Wirkleistung gesamt
    REG_P_L1 = 2
    REG_P_L2 = 4
    REG_P_L3 = 6
    UNIT_ID = 1
    SCALE = 1.0          # z.B. 1.0 wenn Watt, 0.001 wenn mW, 10 wenn 0.1W ...

    def __init__(self, mode="mock", host="", port=502, **kw):
        self.mode = mode
        self.host = host
        self.port = port
        self.opts = kw
        self._client = None
        # mock-state
        self._t0 = time.time()

    async def read(self) -> MeterReading:
        if self.mode == "mock":
            return self._read_mock()
        if self.mode == "modbus":
            return await self._read_modbus()
        if self.mode == "json":
            return await self._read_json()
        return MeterReading(ok=False)

    # ── MOCK: PV-Tagesgang, damit die Regelung was zu tun hat ──
    def _read_mock(self) -> MeterReading:
        # simuliere Haushalt (~600W) + PV-Bogen ueber "Tag" (300s = 1 Tag)
        t = (time.time() - self._t0)
        day = (t % 300) / 300.0
        pv = max(0.0, math.sin(day * math.pi)) * 9000.0      # bis 9 kW PV
        load = 600 + random.uniform(-100, 100)
        miner = float(self.opts.get("miner_draw_cb", lambda: 0.0)())
        grid = load + miner - pv                              # + Bezug / - Einspeisung
        r = MeterReading(ok=True, grid_w=grid, pv_w=pv, ts=time.time())
        r.l1_w = r.l2_w = r.l3_w = grid / 3.0
        return r

    async def _read_modbus(self) -> MeterReading:
        try:
            from pymodbus.client import AsyncModbusTcpClient
            if self._client is None:
                self._client = AsyncModbusTcpClient(self.host, port=self.port)
            if not self._client.connected:
                await self._client.connect()

            async def rd(addr):
                # pymodbus 3.9+: device_id=  (aeltere: slave=) -> siehe Hausanlage-Fix
                try:
                    rr = await self._client.read_holding_registers(addr, count=2, device_id=self.UNIT_ID)
                except TypeError:
                    rr = await self._client.read_holding_registers(addr, count=2, slave=self.UNIT_ID)
                if rr.isError():
                    raise IOError(str(rr))
                # 2 Register -> 32bit; Reihenfolge je Geraet ggf. tauschen
                hi, lo = rr.registers[0], rr.registers[1]
                return ((hi << 16) | lo)

            total = await rd(self.REG_P_TOTAL)
            # 32bit signed
            if total >= 2**31:
                total -= 2**32
            r = MeterReading(ok=True, grid_w=total * self.SCALE, ts=time.time())
            try:
                r.l1_w = await rd(self.REG_P_L1) * self.SCALE
                r.l2_w = await rd(self.REG_P_L2) * self.SCALE
                r.l3_w = await rd(self.REG_P_L3) * self.SCALE
            except Exception:
                r.l1_w = r.l2_w = r.l3_w = r.grid_w / 3.0
            return r
        except Exception as e:
            print(f"[meter] modbus read failed: {e}")
            return MeterReading(ok=False, ts=time.time())

    async def _read_json(self) -> MeterReading:
        try:
            import httpx
            url = self.opts.get("json_url") or f"http://{self.host}/json"
            async with httpx.AsyncClient(timeout=5) as c:
                j = (await c.get(url)).json()
            # Feldnamen aus der Anleitung anpassen:
            key = self.opts.get("json_power_key", "power_total")
            grid = float(j.get(key, 0))
            return MeterReading(ok=True, grid_w=grid, ts=time.time())
        except Exception as e:
            print(f"[meter] json read failed: {e}")
            return MeterReading(ok=False, ts=time.time())
