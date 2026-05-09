from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal


Side = Literal["long", "short"]


@dataclass
class Position:
    exchange: str
    symbol: str
    side: Side
    size: float
    entry_price: float
    mark_price: float
    leverage: float
    liquidation_price: float | None

    @property
    def notional_usd(self) -> float:
        return abs(self.size) * self.mark_price

    @property
    def pnl_usd(self) -> float:
        direction = 1 if self.side == "long" else -1
        return (self.mark_price - self.entry_price) * self.size * direction

    @property
    def delta_usd(self) -> float:
        return self.notional_usd if self.side == "long" else -self.notional_usd


@dataclass
class AccountSnapshot:
    exchange: str
    equity_usd: float
    available_margin_usd: float
    maintenance_margin_usd: float
    positions: list[Position]
    updated_at: datetime


@dataclass
class RiskSnapshot:
    net_delta_usd: float
    margin_ratio: float
    min_liq_distance_pct: float | None
    risk_level: Literal["low", "medium", "high", "critical"]
    warnings: list[str]
    generated_at: datetime


@dataclass
class ConnectorStatus:
    exchange: str
    ok: bool
    error: str | None
    updated_at: datetime


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
