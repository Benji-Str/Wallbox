"""MID-Zaehler am Ladepunkt (Modbus RTU/TCP).

Warum ueberhaupt: Der interne Energiezaehler der Wallbox (DP1/DP25) ist ein
Betriebswert ohne Beglaubigung. Fuer jede Abrechnung — Dienstwagen-Erstattung,
Weiterverrechnung an Mieter, Foerdernachweise — braucht es einen geeichten
MID-Zaehler im Zuleitungsabgang der Wallbox. Dieser hier wird ueber Modbus
gelesen und liefert die Zaehlerstaende fuer das Ladelog.

WICHTIGE ABGRENZUNG: MID-Zaehler + Modbus ist NICHT dasselbe wie
"eichrechtskonform" nach deutschem Mess- und Eichgesetz. Fuer den Verkauf von
Ladestrom an Dritte braucht es eine eichrechtskonforme Ladeeinrichtung mit
signierten Messdaten und Transparenzsoftware. Fuer interne Abrechnung,
Eigenverbrauchsnachweis und Erstattung reicht der MID-Zaehler.

Registerkarten sind je Hersteller verschieden — Voreinstellungen unten sind
die gaengigen; im Zweifel gegen das Datenblatt pruefen (`python3 -m
meter.mid <ip> <port> <unit>` liest und zeigt an, was ankommt).
"""
from __future__ import annotations
import struct
from dataclasses import dataclass

#: Voreinstellungen: (Register Energie kWh, Register Leistung W, Funktionscode)
#: Eastron-Zaehler liefern float32 ueber Input Register (FC4).
PRESETS = {
    # SDM630 / SDM72D-M / SDM120 — verbreitetste MID-Zaehler an Wallboxen
    "eastron": {"reg_energy": 342, "reg_power": 52, "fc": 4,
                "energy_type": "f32", "power_type": "f32",
                "energy_scale": 1.0, "power_scale": 1.0, "word_order": "big"},
    # ABB B23/B24
    "abb": {"reg_energy": 0x5000, "reg_power": 0x5B14, "fc": 3,
            "energy_type": "u64", "power_type": "i32",
            "energy_scale": 0.01, "power_scale": 0.01, "word_order": "big"},
    # Finder 7M
    "finder": {"reg_energy": 406, "reg_power": 140, "fc": 3,
               "energy_type": "u32", "power_type": "i32",
               "energy_scale": 0.1, "power_scale": 1.0, "word_order": "big"},
}


@dataclass
class MidReading:
    ok: bool = False
    energy_kwh: float = 0.0
    power_w: float = 0.0
    error: str = ""


# ---------------------------------------------------------------- Dekodierung
def decode(regs, kind: str, word_order: str = "big", scale: float = 1.0) -> float:
    """Registerwoerter in eine Zahl umrechnen.

    Die Wortreihenfolge ist die haeufigste Fehlerquelle bei Modbus-Zaehlern:
    manche Geraete liefern das hoeherwertige Wort zuerst, manche zuletzt.
    Steht der Zaehlerstand bei 0 oder absurd hoch, ist meist das die Ursache.
    """
    ws = list(regs)
    if word_order == "little":
        ws = ws[::-1]
    raw = b"".join(struct.pack(">H", w & 0xFFFF) for w in ws)
    if kind == "f32":
        val = struct.unpack(">f", raw[:4])[0]
    elif kind == "u32":
        val = struct.unpack(">I", raw[:4])[0]
    elif kind == "i32":
        val = struct.unpack(">i", raw[:4])[0]
    elif kind == "u64":
        val = struct.unpack(">Q", raw[:8])[0]
    elif kind == "i16":
        val = struct.unpack(">h", raw[:2])[0]
    else:
        val = struct.unpack(">H", raw[:2])[0]
    return float(val) * scale


def words_needed(kind: str) -> int:
    return {"f32": 2, "u32": 2, "i32": 2, "u64": 4}.get(kind, 1)


# ---------------------------------------------------------------- Zaehler
class MidMeter:
    """Liest Zaehlerstand und Momentanleistung eines MID-Zaehlers.

    Config (config.json, im Ladepunkt unter "mid_meter"):
      {"preset":"eastron","mode":"tcp","host":"192.168.1.50","port":502,"unit":1}
      {"preset":"eastron","mode":"rtu","device":"/dev/ttyUSB0","baud":9600,"unit":1}
    Einzelne Felder der Voreinstellung koennen ueberschrieben werden.
    """

    def __init__(self, preset="eastron", mode="tcp", host="", port=502,
                 device="", baud=9600, unit=1, start_kwh=0.0, **over):
        self.map = dict(PRESETS.get(preset, PRESETS["eastron"]))
        self.map.update({k: v for k, v in over.items() if k in self.map})
        self.mode, self.host, self.port = mode, host, int(port)
        self.device, self.baud, self.unit = device, int(baud), int(unit)
        self._cli = None
        # Simulation: zaehlt die Leistung mit, die power_cb liefert
        self._mock_kwh = float(start_kwh)
        self._mock_t = None
        self.power_cb = None

    def _mock(self) -> MidReading:
        import time
        now = time.time()
        p = float(self.power_cb() if self.power_cb else 0.0)
        if self._mock_t is not None:
            self._mock_kwh += p * (now - self._mock_t) / 3_600_000.0
        self._mock_t = now
        return MidReading(ok=True, energy_kwh=round(self._mock_kwh, 3), power_w=round(p, 1))

    def _client(self):
        if self._cli is not None:
            return self._cli
        if self.mode == "rtu":
            from pymodbus.client import ModbusSerialClient
            self._cli = ModbusSerialClient(port=self.device, baudrate=self.baud,
                                           parity="N", stopbits=1, bytesize=8, timeout=3)
        else:
            from pymodbus.client import ModbusTcpClient
            self._cli = ModbusTcpClient(self.host, port=self.port, timeout=3)
        self._cli.connect()
        return self._cli

    def _read(self, cli, reg: int, kind: str):
        n = words_needed(kind)
        fn = cli.read_input_registers if self.map["fc"] == 4 else cli.read_holding_registers
        rr = fn(reg, count=n, slave=self.unit)
        if rr.isError():
            raise RuntimeError(f"Register {reg}: {rr}")
        return rr.registers

    def read_sync(self) -> MidReading:
        if self.mode == "mock":
            return self._mock()
        m = self.map
        try:
            cli = self._client()
            e = decode(self._read(cli, m["reg_energy"], m["energy_type"]),
                       m["energy_type"], m["word_order"], m["energy_scale"])
            p = decode(self._read(cli, m["reg_power"], m["power_type"]),
                       m["power_type"], m["word_order"], m["power_scale"])
            return MidReading(ok=True, energy_kwh=round(e, 3), power_w=round(p, 1))
        except Exception as ex:
            self._cli = None            # beim naechsten Mal neu verbinden
            return MidReading(ok=False, error=f"{type(ex).__name__}: {ex}")

    async def read(self) -> MidReading:
        import asyncio
        return await asyncio.to_thread(self.read_sync)


if __name__ == "__main__":       # Register gegen das Datenblatt pruefen
    import sys
    a = sys.argv[1:]
    mm = MidMeter(preset=(a[3] if len(a) > 3 else "eastron"),
                  host=(a[0] if a else "192.168.1.50"),
                  port=int(a[1]) if len(a) > 1 else 502,
                  unit=int(a[2]) if len(a) > 2 else 1)
    r = mm.read_sync()
    print(f"ok={r.ok}  Zaehlerstand={r.energy_kwh} kWh  Leistung={r.power_w} W  {r.error}")
