from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from src.services.history_service import HistoryService


class DailyReportService:
    def __init__(self, history_service: HistoryService) -> None:
        self.history_service = history_service

    def build_report_for_date(self, target_date: date) -> tuple[dict[str, Any], dict[str, Any]] | None:
        snapshots = self.history_service.read_history()
        latest_per_day = {
            self.history_service.history_day_key(snapshot.recorded_at): snapshot
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
