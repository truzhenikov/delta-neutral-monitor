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

    first = datetime(2026, 5, 18, 8, 5, tzinfo=timezone.utc)
    second = datetime(2026, 5, 18, 11, 45, tzinfo=timezone.utc)

    service.record(snapshot=sample_snapshot(recorded_at=first))
    service.record(snapshot=sample_snapshot(recorded_at=second, total_equity_usd=999.0))

    history = service.read_history()

    assert len(history) == 1
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
    assert history[0].recorded_at == fresh_snapshot.recorded_at
    assert history[0].warning_count == 0
