from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from src.connectors.base import ExchangeConnector
from src.core.models import AccountSnapshot
from src.services.monitoring import MonitoringService


class FlakyTimestampConnector(ExchangeConnector):
    exchange = "bingx"

    def __init__(self) -> None:
        self.calls = 0

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("timestamp is invalid")
        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=100.0,
            available_margin_usd=50.0,
            maintenance_margin_usd=10.0,
            positions=[],
            updated_at=datetime(2026, 5, 17, tzinfo=timezone.utc),
        )


class HardFailConnector(ExchangeConnector):
    exchange = "kucoin"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        raise RuntimeError("invalid api key")


def test_monitoring_service_retries_timestamp_errors_once() -> None:
    connector = FlakyTimestampConnector()
    service = MonitoringService([connector])

    accounts, statuses = asyncio.run(service.collect_with_status())

    assert connector.calls == 2
    assert [account.exchange for account in accounts] == ["bingx"]
    assert [(status.exchange, status.ok, status.error) for status in statuses] == [("bingx", True, None)]


def test_monitoring_service_does_not_retry_non_timestamp_errors() -> None:
    service = MonitoringService([HardFailConnector()])

    accounts, statuses = asyncio.run(service.collect_with_status())

    assert accounts == []
    assert len(statuses) == 1
    assert statuses[0].exchange == "kucoin"
    assert statuses[0].ok is False
    assert statuses[0].error == "invalid api key"
