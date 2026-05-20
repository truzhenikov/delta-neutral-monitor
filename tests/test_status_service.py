from __future__ import annotations

from datetime import datetime, timezone

from src.core.models import AccountSnapshot, ConnectorStatus, Position
from src.core.risk import RiskEngine
from src.services.status_service import StatusService


class RecordingHistoryService:
    def __init__(self) -> None:
        self.calls = []

    def record(self, snapshot):
        self.calls.append(snapshot)
        return snapshot


def test_build_status_includes_exchange_level_aggregates() -> None:
    updated_at = datetime(2026, 5, 16, tzinfo=timezone.utc)
    account = AccountSnapshot(
        exchange="bitget",
        equity_usd=1000.0,
        available_margin_usd=650.0,
        maintenance_margin_usd=250.0,
        updated_at=updated_at,
        positions=[
            Position(
                exchange="bitget",
                symbol="BTCUSDT",
                side="long",
                size=0.1,
                entry_price=100000.0,
                mark_price=105000.0,
                leverage=5.0,
                liquidation_price=90000.0,
            ),
            Position(
                exchange="bitget",
                symbol="ETHUSDT",
                side="short",
                size=2.0,
                entry_price=2500.0,
                mark_price=2400.0,
                leverage=4.0,
                liquidation_price=2900.0,
            ),
        ],
    )
    connector_status = ConnectorStatus(
        exchange="bitget",
        ok=True,
        error=None,
        updated_at=updated_at,
    )
    service = StatusService(RiskEngine(max_margin_ratio=0.75, min_liq_distance_pct=12.0, max_abs_net_delta_usd=500.0))

    snapshot = service.build_status([account], [connector_status])

    assert snapshot.accounts[0].position_count == 2
    assert snapshot.accounts[0].total_pnl_usd == 700.0
    assert snapshot.accounts[0].total_delta_usd == 5700.0
    assert snapshot.accounts[0].total_notional_usd == 15300.0
    assert snapshot.accounts[0].load_ratio == 0.25


def test_build_status_includes_embedded_history_snapshot() -> None:
    updated_at = datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc)
    account = AccountSnapshot(
        exchange="bitget",
        equity_usd=1000.0,
        available_margin_usd=600.0,
        maintenance_margin_usd=120.0,
        updated_at=updated_at,
        positions=[],
    )
    connector_status = ConnectorStatus(
        exchange="bitget",
        ok=True,
        error=None,
        updated_at=updated_at,
    )
    service = StatusService(RiskEngine(max_margin_ratio=0.75, min_liq_distance_pct=12.0, max_abs_net_delta_usd=500.0))

    status = service.build_status([account], [connector_status])

    assert status.current_snapshot.total_equity_usd == 1000.0
    assert status.current_snapshot.warning_count == len(status.risk.warnings)
    assert status.current_snapshot.warnings == status.risk.warnings


def test_build_status_skips_history_persistence_when_any_connector_is_stale() -> None:
    updated_at = datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc)
    account = AccountSnapshot(
        exchange="bitget",
        equity_usd=1000.0,
        available_margin_usd=600.0,
        maintenance_margin_usd=120.0,
        updated_at=updated_at,
        positions=[],
    )
    history_service = RecordingHistoryService()
    service = StatusService(
        RiskEngine(max_margin_ratio=0.75, min_liq_distance_pct=12.0, max_abs_net_delta_usd=500.0),
        history_service=history_service,
    )

    status = service.build_status(
        [account],
        [
            ConnectorStatus(exchange="bitget", ok=False, error="timeout", updated_at=updated_at),
        ],
    )

    assert status.current_snapshot.total_equity_usd == 1000.0
    assert history_service.calls == []


def test_build_status_marks_response_as_stale_and_uses_oldest_account_timestamp() -> None:
    older_updated_at = datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc)
    newer_updated_at = datetime(2026, 5, 18, 0, 5, tzinfo=timezone.utc)
    accounts = [
        AccountSnapshot(
            exchange="bitget",
            equity_usd=1000.0,
            available_margin_usd=600.0,
            maintenance_margin_usd=120.0,
            updated_at=newer_updated_at,
            positions=[],
        ),
        AccountSnapshot(
            exchange="okx",
            equity_usd=2000.0,
            available_margin_usd=1500.0,
            maintenance_margin_usd=100.0,
            updated_at=older_updated_at,
            positions=[],
        ),
    ]
    connector_statuses = [
        ConnectorStatus(exchange="bitget", ok=True, error=None, updated_at=newer_updated_at),
        ConnectorStatus(exchange="okx", ok=False, error="timeout", updated_at=newer_updated_at),
    ]
    service = StatusService(RiskEngine(max_margin_ratio=0.75, min_liq_distance_pct=12.0, max_abs_net_delta_usd=500.0))

    status = service.build_status(accounts, connector_statuses)

    assert status.source == "stale"
    assert status.snapshot_updated_at == older_updated_at
