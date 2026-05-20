from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from src.config import get_settings
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


class SuccessThenFailConnector(ExchangeConnector):
    exchange = "okx"

    def __init__(self) -> None:
        self.calls = 0

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        self.calls += 1
        if self.calls == 1:
            return AccountSnapshot(
                exchange=self.exchange,
                equity_usd=250.0,
                available_margin_usd=150.0,
                maintenance_margin_usd=25.0,
                positions=[],
                updated_at=datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc),
            )
        raise RuntimeError("timeout")


class SuccessThenSlowConnector(ExchangeConnector):
    exchange = "extended"

    def __init__(self) -> None:
        self.calls = 0

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        self.calls += 1
        if self.calls == 1:
            return AccountSnapshot(
                exchange=self.exchange,
                equity_usd=400.0,
                available_margin_usd=250.0,
                maintenance_margin_usd=40.0,
                positions=[],
                updated_at=datetime(2026, 5, 19, 11, 0, tzinfo=timezone.utc),
            )
        await asyncio.sleep(0.05)
        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=410.0,
            available_margin_usd=255.0,
            maintenance_margin_usd=41.0,
            positions=[],
            updated_at=datetime(2026, 5, 19, 11, 5, tzinfo=timezone.utc),
        )


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


def test_monitoring_service_reuses_last_snapshot_when_exchange_times_out(tmp_path: Path) -> None:
    connector = SuccessThenFailConnector()
    service = MonitoringService([connector], cache_path=tmp_path / "latest-accounts.json")

    first_accounts, first_statuses = asyncio.run(service.collect_with_status())
    second_accounts, second_statuses = asyncio.run(service.collect_with_status())

    assert [account.exchange for account in first_accounts] == ["okx"]
    assert first_statuses[0].ok is True
    assert [account.exchange for account in second_accounts] == ["okx"]
    assert second_accounts[0].equity_usd == 250.0
    assert second_accounts[0].updated_at == datetime(2026, 5, 19, 10, 0, tzinfo=timezone.utc)
    assert second_statuses[0].ok is False
    assert second_statuses[0].error == "timeout"


def test_monitoring_service_caps_slow_connector_wait_and_reuses_cached_snapshot(tmp_path: Path, monkeypatch) -> None:
    connector = SuccessThenSlowConnector()
    service = MonitoringService([connector], cache_path=tmp_path / "latest-accounts.json")

    get_settings.cache_clear()
    monkeypatch.setenv("REQUEST_TIMEOUT_SEC", "0.01")

    first_accounts, first_statuses = asyncio.run(service.collect_with_status())
    second_accounts, second_statuses = asyncio.run(asyncio.wait_for(service.collect_with_status(), timeout=0.03))

    assert [account.exchange for account in first_accounts] == ["extended"]
    assert first_statuses[0].ok is True
    assert [account.exchange for account in second_accounts] == ["extended"]
    assert second_accounts[0].equity_usd == 400.0
    assert second_accounts[0].updated_at == datetime(2026, 5, 19, 11, 0, tzinfo=timezone.utc)
    assert second_statuses[0].ok is False
    assert "timeout" in second_statuses[0].error.lower()
