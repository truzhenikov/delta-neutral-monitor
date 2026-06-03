from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.core.schemas import PortfolioHistorySnapshotOut
from src.services.history_service import HistoryService


class DailyReportService:
    def __init__(self, history_service: HistoryService) -> None:
        self.history_service = history_service
        self.daily_snapshots_path = Path(history_service.storage_dir) / "daily-snapshots.json"

    def capture_snapshot(self, status: dict[str, Any], now: datetime) -> PortfolioHistorySnapshotOut:
        effective_now = now.astimezone(timezone.utc)
        warnings = list(((status.get("risk") or {}).get("warnings") or []))
        snapshot = PortfolioHistorySnapshotOut(
            recorded_at=effective_now,
            total_equity_usd=float(status.get("total_equity_usd") or 0.0),
            total_available_margin_usd=float(status.get("total_available_margin_usd") or 0.0),
            total_maintenance_margin_usd=float(status.get("total_maintenance_margin_usd") or 0.0),
            warning_count=len(warnings),
            warnings=warnings,
        )
        snapshots = self.read_daily_snapshots()
        target_day_key = self.report_day_key(effective_now)
        retained: list[PortfolioHistorySnapshotOut] = []
        replaced = False
        for item in snapshots:
            if self.report_day_key(item.recorded_at) == target_day_key:
                if not replaced:
                    retained.append(snapshot)
                    replaced = True
                continue
            retained.append(item)
        if not replaced:
            retained.append(snapshot)
        retained.sort(key=lambda item: item.recorded_at)
        self._write_daily_snapshots(retained)
        return snapshot

    def read_daily_snapshots(self) -> list[PortfolioHistorySnapshotOut]:
        if not self.daily_snapshots_path.exists():
            return []
        payload = json.loads(self.daily_snapshots_path.read_text(encoding="utf-8"))
        return [PortfolioHistorySnapshotOut.model_validate(item) for item in payload]

    def build_report_for_date(self, target_date: date) -> tuple[dict[str, Any], dict[str, Any]] | None:
        snapshots = self.read_daily_snapshots()
        latest_per_day = {
            self.report_day_key(snapshot.recorded_at): snapshot
            for snapshot in sorted(snapshots, key=lambda item: item.recorded_at)
        }

        target_day_key = target_date.isoformat()
        current = latest_per_day.get(target_day_key)
        if current is None:
            return None

        previous_candidates = [
            (day_key, snapshot)
            for day_key, snapshot in latest_per_day.items()
            if day_key < target_day_key
        ]
        if not previous_candidates:
            return None

        previous_day_key, previous = max(previous_candidates, key=lambda item: item[0])
        raw_change_usd = current.total_equity_usd - previous.total_equity_usd
        change_usd = round(raw_change_usd, 2)
        change_pct = 0.0 if previous.total_equity_usd == 0 else (raw_change_usd / previous.total_equity_usd) * 100

        current_payload = {
            "date": target_day_key,
            "equity_usd": current.total_equity_usd,
            "change_usd": change_usd,
            "change_pct": change_pct,
            "warning_count": current.warning_count,
            "warnings": current.warnings,
        }
        previous_payload = {
            "date": previous_day_key,
            "equity_usd": previous.total_equity_usd,
            "change_usd": None,
            "change_pct": None,
            "warning_count": previous.warning_count,
            "warnings": previous.warnings,
        }
        return current_payload, previous_payload

    def build_latest_report(self) -> tuple[dict[str, Any], dict[str, Any]] | None:
        snapshots = self.read_daily_snapshots()
        if len(snapshots) < 2:
            return None
        latest_day = self.report_day_key(max(snapshots, key=lambda item: item.recorded_at).recorded_at)
        return self.build_report_for_date(date.fromisoformat(latest_day))

    def build_recent_reports(self, limit: int = 10) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        snapshots = self.read_daily_snapshots()
        day_keys = sorted({self.report_day_key(item.recorded_at) for item in snapshots}, reverse=True)
        reports: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for day_key in day_keys:
            report = self.build_report_for_date(date.fromisoformat(day_key))
            if report is not None:
                reports.append(report)
            if len(reports) >= limit:
                break
        return reports

    def report_day_key(self, now: datetime) -> str:
        return self.history_service.history_day_key(now)

    def should_send(self, chat_settings: dict[str, Any], now: datetime) -> bool:
        if not chat_settings.get("daily_report_enabled"):
            return False
        now_utc = now.astimezone(timezone.utc)
        scheduled_hour = int(chat_settings.get("daily_report_hour_utc", 7))
        if now_utc.hour < scheduled_hour:
            return False
        return chat_settings.get("last_daily_report_date") != self.report_day_key(now)

    def _write_daily_snapshots(self, snapshots: list[PortfolioHistorySnapshotOut]) -> None:
        self.daily_snapshots_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [item.model_dump(mode="json") for item in snapshots]
        self.daily_snapshots_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
