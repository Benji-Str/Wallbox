"""Gemeinsames Miner-Interface — jede Marke implementiert genau diese Methoden.

Die Regelung ruft nur diese vier Dinge auf und weiss NICHTS von der Marke:
    stats  = await miner.get_stats()   -> MinerStats
    await miner.set_power(watt)         -> Ziel-Leistung setzen (so fein wie moeglich)
    await miner.pause()                 -> Mining stoppen (leise/aus)
    await miner.resume()               -> Mining wieder starten

So kommen neue Marken dazu, ohne die Regelung anzufassen.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MinerStats:
    ip: str
    online: bool = False
    power_w: float = 0.0          # aktuelle Leistungsaufnahme (W)
    hashrate_ths: float = 0.0     # aktuelle Hashrate (TH/s)
    temp_c: float = 0.0           # heisster Chip / Board (C)
    fan_pct: float = 0.0          # Luefter (%)
    state: str = "unknown"        # mining / paused / offline / error
    model: str = ""
    raw: dict = field(default_factory=dict)


class MinerDriver:
    """Basisklasse. Attribute: ip, name, min_w, max_w, granularity."""

    #: kleinste sinnvolle Leistungsstufe (W). Braiins/WhatsMiner ~ fein, Avalon grob.
    granularity_w: int = 100
    #: True wenn die Marke Watt-genau kann, False bei reinen Stufen (Avalon workmode)
    stepwise: bool = False

    def __init__(self, ip: str, name: str = "", user: str = "",
                 password: str = "", min_w: int = 0, max_w: int = 3500, **kw):
        self.ip = ip
        self.name = name or ip
        self.user = user
        self.password = password
        self.min_w = min_w
        self.max_w = max_w
        self.opts = kw

    async def get_stats(self) -> MinerStats:
        raise NotImplementedError

    async def set_power(self, watt: int) -> bool:
        raise NotImplementedError

    async def pause(self) -> bool:
        raise NotImplementedError

    async def resume(self) -> bool:
        raise NotImplementedError

    # bequemer Repr fuers Log
    def __repr__(self):
        return f"<{self.__class__.__name__} {self.name} {self.ip}>"


def clamp_power(driver: MinerDriver, watt: int) -> int:
    """Ziel-Watt auf [min_w, max_w] begrenzen und auf Granularitaet runden."""
    watt = max(driver.min_w, min(driver.max_w, int(watt)))
    g = max(1, driver.granularity_w)
    return round(watt / g) * g
