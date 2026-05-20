from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field


class PositionOut(BaseModel):
    exchange: str
    symbol: str
    side: Literal["long", "short"]
    size: float
    entry_price: float
    mark_price: float
    leverage: float
    liquidation_price: Optional[float]
    notional_usd: float
    pnl_usd: float
    delta_usd: float


class AccountOut(BaseModel):
    exchange: str
    equity_usd: float
    available_margin_usd: float
    maintenance_margin_usd: float
    position_count: int
    total_notional_usd: float
    total_pnl_usd: float
    total_delta_usd: float
    load_ratio: float = Field(ge=0)
    updated_at: datetime
    positions: list[PositionOut]


class RiskOut(BaseModel):
    net_delta_usd: float
    margin_ratio: float = Field(ge=0)
    min_liq_distance_pct: Optional[float]
    risk_level: Literal["low", "medium", "high", "critical"]
    warnings: list[str]
    generated_at: datetime


class ConnectorStatusOut(BaseModel):
    exchange: str
    ok: bool
    error: Optional[str]
    updated_at: datetime


class PortfolioHistorySnapshotOut(BaseModel):
    recorded_at: datetime
    total_equity_usd: float
    total_available_margin_usd: float
    total_maintenance_margin_usd: float
    warning_count: int = Field(ge=0)
    warnings: list[str]


class HistoryChartPointOut(BaseModel):
    label: str
    equity_usd: float
    recorded_at: datetime


class PortfolioHistorySummaryOut(BaseModel):
    date: str
    equity_usd: float
    change_usd: Optional[float]
    warning_count: int = Field(ge=0)
    warnings: list[str]


class PortfolioHistoryOut(BaseModel):
    snapshots: list[PortfolioHistorySnapshotOut]
    chart: list[HistoryChartPointOut]
    daily_changes: list[PortfolioHistorySummaryOut]


class StatusOut(BaseModel):
    total_equity_usd: float
    total_available_margin_usd: float
    total_maintenance_margin_usd: float
    accounts: list[AccountOut]
    connector_statuses: list[ConnectorStatusOut]
    risk: RiskOut
    current_snapshot: PortfolioHistorySnapshotOut
    source: Literal["live", "stale"]
    snapshot_updated_at: datetime
