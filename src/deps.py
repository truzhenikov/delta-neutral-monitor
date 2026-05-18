from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from src.config import get_settings
from src.connectors.factory import build_connectors
from src.core.risk import RiskEngine
from src.services.alerting import AlertingService
from src.services.history_service import HistoryService
from src.services.monitoring import MonitoringService
from src.services.status_service import StatusService


@lru_cache(maxsize=1)
def get_monitoring_service() -> MonitoringService:
    settings = get_settings()
    return MonitoringService(
        connectors=build_connectors(
            settings.exchanges,
            use_mock_data=settings.use_mock_data,
        )
    )


@lru_cache(maxsize=1)
def get_history_service() -> HistoryService:
    settings = get_settings()
    return HistoryService(
        storage_dir=Path(settings.history_storage_dir),
        interval_hours=settings.history_snapshot_interval_hours,
        retention_days=settings.history_retention_days,
    )


@lru_cache(maxsize=1)
def get_status_service() -> StatusService:
    settings = get_settings()
    risk_engine = RiskEngine(
        max_margin_ratio=settings.max_margin_ratio,
        min_liq_distance_pct=settings.min_liq_distance_pct,
        max_abs_net_delta_usd=settings.max_abs_net_delta_usd,
    )
    return StatusService(risk_engine=risk_engine, history_service=get_history_service())


@lru_cache(maxsize=1)
def get_alerting_service() -> AlertingService:
    settings = get_settings()
    return AlertingService(cooldown_sec=settings.alert_cooldown_sec)
