from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from src.core.schemas import PortfolioHistorySnapshotOut
from src.services.history_service import HistoryService
from src.services.telegram_preferences import TelegramPreferencesService


def build_history_service(tmp_path: Path) -> HistoryService:
    service = HistoryService(storage_dir=tmp_path, interval_hours=1, retention_days=30)
    snapshots = [
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 17, 9, 0, tzinfo=timezone.utc),
            total_equity_usd=41000.0,
            total_available_margin_usd=15000.0,
            total_maintenance_margin_usd=6000.0,
            warning_count=0,
            warnings=[],
        ),
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 17, 23, 0, tzinfo=timezone.utc),
            total_equity_usd=41250.0,
            total_available_margin_usd=15100.0,
            total_maintenance_margin_usd=6050.0,
            warning_count=1,
            warnings=["warn-prev"],
        ),
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 18, 8, 0, tzinfo=timezone.utc),
            total_equity_usd=41800.0,
            total_available_margin_usd=15200.0,
            total_maintenance_margin_usd=6100.0,
            warning_count=0,
            warnings=[],
        ),
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 18, 22, 0, tzinfo=timezone.utc),
            total_equity_usd=42126.34,
            total_available_margin_usd=15300.0,
            total_maintenance_margin_usd=6150.0,
            warning_count=2,
            warnings=["warn-current-1", "warn-current-2"],
        ),
    ]
    for snapshot in snapshots:
        service.record(snapshot, now=snapshot.recorded_at)
    return service


def test_build_report_for_date_selects_latest_snapshot_per_day(tmp_path: Path) -> None:
    from src.services.daily_report_service import DailyReportService

    history_service = build_history_service(tmp_path)
    service = DailyReportService(history_service=history_service)

    current, previous = service.build_report_for_date(date(2026, 5, 18))

    assert current["date"] == "2026-05-18"
    assert current["equity_usd"] == 42126.34
    assert previous["date"] == "2026-05-17"
    assert previous["equity_usd"] == 41250.0


def test_build_report_for_date_computes_change_fields_from_previous_day(tmp_path: Path) -> None:
    from src.services.daily_report_service import DailyReportService

    history_service = build_history_service(tmp_path)
    service = DailyReportService(history_service=history_service)

    current, previous = service.build_report_for_date(date(2026, 5, 18))

    assert current["change_usd"] == 876.34
    assert round(current["change_pct"], 6) == round((876.34 / 41250.0) * 100, 6)
    assert previous["change_usd"] is None


def test_build_report_for_date_uses_5am_moscow_day_boundary(tmp_path: Path) -> None:
    from src.services.daily_report_service import DailyReportService

    history_service = HistoryService(storage_dir=tmp_path, interval_hours=1, retention_days=30)
    snapshots = [
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc),
            total_equity_usd=1000.0,
            total_available_margin_usd=600.0,
            total_maintenance_margin_usd=120.0,
            warning_count=0,
            warnings=[],
        ),
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 18, 4, 0, tzinfo=timezone.utc),
            total_equity_usd=1100.0,
            total_available_margin_usd=650.0,
            total_maintenance_margin_usd=125.0,
            warning_count=1,
            warnings=["warn-a"],
        ),
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 19, 0, 0, tzinfo=timezone.utc),
            total_equity_usd=1300.0,
            total_available_margin_usd=700.0,
            total_maintenance_margin_usd=130.0,
            warning_count=1,
            warnings=["warn-b"],
        ),
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 19, 4, 0, tzinfo=timezone.utc),
            total_equity_usd=1400.0,
            total_available_margin_usd=760.0,
            total_maintenance_margin_usd=135.0,
            warning_count=0,
            warnings=[],
        ),
    ]
    for snapshot in snapshots:
        history_service.record(snapshot, now=snapshot.recorded_at)

    service = DailyReportService(history_service=history_service)

    current, previous = service.build_report_for_date(date(2026, 5, 19))

    assert current["date"] == "2026-05-19"
    assert current["equity_usd"] == 1400.0
    assert current["change_usd"] == 100.0
    assert previous["date"] == "2026-05-18"
    assert previous["equity_usd"] == 1300.0


def test_build_report_for_date_returns_none_without_previous_day(tmp_path: Path) -> None:
    from src.services.daily_report_service import DailyReportService

    history_service = HistoryService(storage_dir=tmp_path, interval_hours=1, retention_days=30)
    history_service.record(
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 18, 22, 0, tzinfo=timezone.utc),
            total_equity_usd=42126.34,
            total_available_margin_usd=15300.0,
            total_maintenance_margin_usd=6150.0,
            warning_count=0,
            warnings=[],
        ),
        now=datetime(2026, 5, 18, 22, 0, tzinfo=timezone.utc),
    )
    service = DailyReportService(history_service=history_service)

    assert service.build_report_for_date(date(2026, 5, 18)) is None


def test_should_send_respects_enabled_hour_and_last_sent_date(tmp_path: Path) -> None:
    from src.services.daily_report_service import DailyReportService

    history_service = build_history_service(tmp_path)
    service = DailyReportService(history_service=history_service)
    prefs = TelegramPreferencesService(state_path=tmp_path / "telegram-state.json", admin_chat_ids=[], daily_report_hour_utc=7)
    prefs.set_daily_report_enabled("123", True)

    early = datetime(2026, 5, 18, 6, 59, tzinfo=timezone.utc)
    on_time = datetime(2026, 5, 18, 7, 0, tzinfo=timezone.utc)

    assert service.should_send(prefs.get_chat("123"), early) is False
    assert service.should_send(prefs.get_chat("123"), on_time) is True

    prefs.mark_daily_report_sent("123", "2026-05-18")

    assert service.should_send(prefs.get_chat("123"), on_time) is False


def test_should_send_uses_5am_moscow_business_day_for_dedupe(tmp_path: Path) -> None:
    from src.services.daily_report_service import DailyReportService

    history_service = build_history_service(tmp_path)
    service = DailyReportService(history_service=history_service)
    prefs = TelegramPreferencesService(state_path=tmp_path / "telegram-state.json", admin_chat_ids=[], daily_report_hour_utc=1)
    prefs.set_daily_report_enabled("123", True)

    before_cutoff = datetime(2026, 5, 19, 1, 5, tzinfo=timezone.utc)
    after_cutoff = datetime(2026, 5, 19, 2, 5, tzinfo=timezone.utc)

    assert service.should_send(prefs.get_chat("123"), before_cutoff) is True

    prefs.mark_daily_report_sent("123", "2026-05-18")

    assert service.should_send(prefs.get_chat("123"), before_cutoff) is False
    assert service.should_send(prefs.get_chat("123"), after_cutoff) is True


def test_send_due_daily_reports_sends_once_and_marks_sent(tmp_path: Path) -> None:
    from src.bot.run import send_due_daily_reports
    from src.services.daily_report_service import DailyReportService

    history_service = build_history_service(tmp_path)
    daily_service = DailyReportService(history_service=history_service)
    prefs = TelegramPreferencesService(state_path=tmp_path / "telegram-state.json", admin_chat_ids=[], daily_report_hour_utc=7)
    prefs.set_daily_report_enabled("123", True)
    prefs.set_daily_report_enabled("456", False)
    bot = AsyncMock()
    now = datetime(2026, 5, 18, 7, 5, tzinfo=timezone.utc)

    asyncio.run(send_due_daily_reports(bot, prefs, daily_service, now=now))
    asyncio.run(send_due_daily_reports(bot, prefs, daily_service, now=now))

    sent_messages = [kwargs for _, kwargs in bot.send_message.await_args_list]
    assert len(sent_messages) == 1
    assert sent_messages[0]["chat_id"] == "123"
    assert "Daily portfolio report" in sent_messages[0]["text"]
    assert prefs.get_chat("123")["last_daily_report_date"] == "2026-05-18"


def test_send_due_daily_reports_does_not_mark_sent_on_send_failure(tmp_path: Path) -> None:
    from src.bot.run import send_due_daily_reports
    from src.services.daily_report_service import DailyReportService

    history_service = build_history_service(tmp_path)
    daily_service = DailyReportService(history_service=history_service)
    prefs = TelegramPreferencesService(state_path=tmp_path / "telegram-state.json", admin_chat_ids=[], daily_report_hour_utc=7)
    prefs.set_daily_report_enabled("123", True)
    bot = AsyncMock()
    bot.send_message.side_effect = TimeoutError("telegram timeout")
    now = datetime(2026, 5, 18, 7, 5, tzinfo=timezone.utc)

    asyncio.run(send_due_daily_reports(bot, prefs, daily_service, now=now))

    assert bot.send_message.await_count == 1
    assert prefs.get_chat("123").get("last_daily_report_date") is None


def test_send_due_daily_reports_uses_business_day_key_for_report_selection_and_dedupe(tmp_path: Path) -> None:
    from src.bot.run import send_due_daily_reports
    from src.services.daily_report_service import DailyReportService

    history_service = HistoryService(storage_dir=tmp_path, interval_hours=1, retention_days=30)
    snapshots = [
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 17, 23, 0, tzinfo=timezone.utc),
            total_equity_usd=1200.0,
            total_available_margin_usd=600.0,
            total_maintenance_margin_usd=120.0,
            warning_count=0,
            warnings=[],
        ),
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 18, 23, 0, tzinfo=timezone.utc),
            total_equity_usd=1300.0,
            total_available_margin_usd=650.0,
            total_maintenance_margin_usd=125.0,
            warning_count=1,
            warnings=["warn-business-day"],
        ),
        PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 19, 1, 0, tzinfo=timezone.utc),
            total_equity_usd=1350.0,
            total_available_margin_usd=680.0,
            total_maintenance_margin_usd=128.0,
            warning_count=0,
            warnings=[],
        ),
    ]
    for snapshot in snapshots:
        history_service.record(snapshot, now=snapshot.recorded_at)

    daily_service = DailyReportService(history_service=history_service)
    prefs = TelegramPreferencesService(state_path=tmp_path / "telegram-state.json", admin_chat_ids=[], daily_report_hour_utc=1)
    prefs.set_daily_report_enabled("123", True)
    bot = AsyncMock()
    now = datetime(2026, 5, 19, 1, 5, tzinfo=timezone.utc)

    asyncio.run(send_due_daily_reports(bot, prefs, daily_service, now=now))
    asyncio.run(send_due_daily_reports(bot, prefs, daily_service, now=now))

    sent_messages = [kwargs for _, kwargs in bot.send_message.await_args_list]
    assert len(sent_messages) == 1
    assert sent_messages[0]["chat_id"] == "123"
    assert "2026-05-18" in sent_messages[0]["text"]
    assert prefs.get_chat("123")["last_daily_report_date"] == "2026-05-18"
