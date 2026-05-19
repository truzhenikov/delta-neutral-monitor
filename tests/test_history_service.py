from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from src.core.schemas import PortfolioHistorySnapshotOut
from src.services.history_service import HistoryService


def sample_snapshot(*, recorded_at: datetime, total_equity_usd: float = 1000.0, warnings: list[str] | None = None) -> PortfolioHistorySnapshotOut:
    effective_warnings = ["warning-a"] if warnings is None else warnings
    return PortfolioHistorySnapshotOut(
        recorded_at=recorded_at,
        total_equity_usd=total_equity_usd,
        total_available_margin_usd=600.0,
        total_maintenance_margin_usd=120.0,
        warning_count=len(effective_warnings),
        warnings=effective_warnings,
    )


def test_history_service_persists_one_snapshot_per_4h_bucket(tmp_path: Path) -> None:
    service = HistoryService(storage_dir=tmp_path, interval_hours=4, retention_days=30)

    first = datetime(2026, 5, 18, 10, 5, tzinfo=timezone.utc)
    second = datetime(2026, 5, 18, 13, 45, tzinfo=timezone.utc)

    service.record(snapshot=sample_snapshot(recorded_at=first))
    service.record(snapshot=sample_snapshot(recorded_at=second, total_equity_usd=999.0))

    history = service.read_history()

    assert len(history) == 1
    assert history[0].recorded_at == datetime(2026, 5, 18, 10, 0, tzinfo=timezone.utc)
    assert history[0].total_equity_usd == 999.0
    assert history[0].warnings == ["warning-a"]


def test_history_service_discards_snapshots_older_than_retention_window(tmp_path: Path) -> None:
    service = HistoryService(storage_dir=tmp_path, interval_hours=4, retention_days=2)

    old_snapshot = sample_snapshot(recorded_at=datetime(2026, 5, 10, 0, 0, tzinfo=timezone.utc))
    fresh_snapshot = sample_snapshot(recorded_at=datetime(2026, 5, 12, 4, 0, tzinfo=timezone.utc), total_equity_usd=1500.0, warnings=[])

    service.record(snapshot=old_snapshot, now=datetime(2026, 5, 12, 6, 0, tzinfo=timezone.utc))
    service.record(snapshot=fresh_snapshot, now=datetime(2026, 5, 12, 6, 0, tzinfo=timezone.utc))

    history = service.read_history()

    assert len(history) == 1
    assert history[0].recorded_at == datetime(2026, 5, 12, 2, 0, tzinfo=timezone.utc)
    assert history[0].warning_count == 0


def test_history_service_groups_daily_changes_by_5am_moscow_boundary(tmp_path: Path) -> None:
    service = HistoryService(storage_dir=tmp_path, interval_hours=4, retention_days=30)

    service.record(snapshot=sample_snapshot(recorded_at=datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc), total_equity_usd=1000.0, warnings=[]))
    service.record(snapshot=sample_snapshot(recorded_at=datetime(2026, 5, 18, 4, 0, tzinfo=timezone.utc), total_equity_usd=1100.0, warnings=["warning-b"]))
    service.record(snapshot=sample_snapshot(recorded_at=datetime(2026, 5, 19, 0, 0, tzinfo=timezone.utc), total_equity_usd=1300.0, warnings=["warning-c"]))
    service.record(snapshot=sample_snapshot(recorded_at=datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc), total_equity_usd=1400.0, warnings=[]))

    payload = service.build_history_response()

    assert [row.date for row in payload.daily_changes] == ["2026-05-19", "2026-05-18", "2026-05-17"]
    assert payload.daily_changes[0].equity_usd == 1400.0
    assert payload.daily_changes[0].change_usd == 100.0
    assert payload.daily_changes[1].equity_usd == 1300.0
    assert payload.daily_changes[1].warning_count == 1
    assert payload.daily_changes[2].equity_usd == 1000.0
