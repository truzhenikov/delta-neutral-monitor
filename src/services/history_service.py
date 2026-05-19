from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.core.schemas import (
    HistoryChartPointOut,
    PortfolioHistoryOut,
    PortfolioHistorySnapshotOut,
    PortfolioHistorySummaryOut,
)


class HistoryService:
    def __init__(self, storage_dir: Path, interval_hours: int = 4, retention_days: int = 30) -> None:
        self.storage_dir = storage_dir
        self.interval_hours = interval_hours
        self.retention_days = retention_days
        self.history_path = self.storage_dir / "portfolio-history.json"

    def record(self, snapshot: PortfolioHistorySnapshotOut, now: datetime | None = None) -> PortfolioHistorySnapshotOut:
        history = self.read_history()
        effective_now = now or snapshot.recorded_at
        cutoff = effective_now - timedelta(days=self.retention_days)
        bucketed_snapshot = snapshot.model_copy(update={"recorded_at": self._normalize_bucket(snapshot.recorded_at)})

        retained = [item for item in history if item.recorded_at >= cutoff]
        replaced = False
        for index, item in enumerate(retained):
            if item.recorded_at == bucketed_snapshot.recorded_at:
                retained[index] = bucketed_snapshot
                replaced = True
                break

        if not replaced:
            retained.append(bucketed_snapshot)

        retained.sort(key=lambda item: item.recorded_at)
        self._write_history(retained)
        return bucketed_snapshot

    def read_history(self) -> list[PortfolioHistorySnapshotOut]:
        if not self.history_path.exists():
            return []
        payload = json.loads(self.history_path.read_text(encoding="utf-8"))
        return [PortfolioHistorySnapshotOut.model_validate(item) for item in payload]

    def build_history_response(self) -> PortfolioHistoryOut:
        snapshots = self.read_history()
        chart = [
            HistoryChartPointOut(
                label=item.recorded_at.strftime("%Y-%m-%d %H:%M"),
                equity_usd=item.total_equity_usd,
                recorded_at=item.recorded_at,
            )
            for item in snapshots
        ]

        latest_per_day: dict[str, PortfolioHistorySnapshotOut] = {}
        for item in snapshots:
            latest_per_day[self.history_day_key(item.recorded_at)] = item

        ordered_days = sorted(latest_per_day.items())
        daily_changes: list[PortfolioHistorySummaryOut] = []
        previous_equity: float | None = None
        for day, item in ordered_days:
            change_usd = None if previous_equity is None else item.total_equity_usd - previous_equity
            daily_changes.append(
                PortfolioHistorySummaryOut(
                    date=day,
                    equity_usd=item.total_equity_usd,
                    change_usd=change_usd,
                    warning_count=item.warning_count,
                    warnings=item.warnings,
                )
            )
            previous_equity = item.total_equity_usd

        return PortfolioHistoryOut(snapshots=snapshots, chart=chart, daily_changes=list(reversed(daily_changes)))

    def history_day_key(self, value: datetime) -> str:
        utc_value = value.astimezone(timezone.utc)
        shifted = utc_value - timedelta(hours=2)
        return shifted.date().isoformat()

    def _normalize_bucket(self, value: datetime) -> datetime:
        utc_value = value.astimezone(timezone.utc)
        shifted = utc_value - timedelta(hours=2)
        bucket_hour = (shifted.hour // self.interval_hours) * self.interval_hours
        return shifted.replace(hour=bucket_hour, minute=0, second=0, microsecond=0) + timedelta(hours=2)

    def _write_history(self, snapshots: list[PortfolioHistorySnapshotOut]) -> None:
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump(mode="json") for item in snapshots]
        self.history_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
