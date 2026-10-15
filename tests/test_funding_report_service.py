from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

from src.services.credential_store import CredentialStore

MSK = ZoneInfo("Europe/Moscow")


def build_store(path: Path, *, with_lighter: bool = True) -> CredentialStore:
    store = CredentialStore(path)
    store.set_exchange_credentials("hyperliquid:main", {"user_address": "0xabc"})
    if with_lighter:
        store.set_exchange_credentials("lighter:main", {"account_index": "123", "l1_address": "0xdef"})
    return store


def test_completed_window_uses_latest_finished_1130_msk_period(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReportService

    service = FundingReportService(build_store(tmp_path / "credentials.json"), tmp_path / "funding.json")

    start, end = service.completed_window(datetime(2026, 10, 15, 12, 0, tzinfo=MSK))
    assert start == datetime(2026, 10, 14, 11, 30, tzinfo=MSK)
    assert end == datetime(2026, 10, 15, 11, 30, tzinfo=MSK)

    start, end = service.completed_window(datetime(2026, 10, 15, 10, 0, tzinfo=MSK))
    assert start == datetime(2026, 10, 13, 11, 30, tzinfo=MSK)
    assert end == datetime(2026, 10, 14, 11, 30, tzinfo=MSK)


def test_hyperliquid_funding_is_aggregated_by_exchange_and_coin(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReportService

    async def request_json(method: str, url: str, **kwargs):
        assert method == "POST"
        assert url.endswith("/info")
        assert kwargs["json"]["type"] == "userFunding"
        start_ms = kwargs["json"]["startTime"]
        return [
            {"time": start_ms + 1, "delta": {"coin": "io:NBIS", "usdc": "1.25"}},
            {"time": start_ms + 2, "delta": {"coin": "io:NBIS", "usdc": "-0.20"}},
            {"time": start_ms + 3, "delta": {"coin": "xyz:PLATINUM", "usdc": "2.00"}},
        ]

    service = FundingReportService(
        build_store(tmp_path / "credentials.json"),
        tmp_path / "funding.json",
        request_json=request_json,
    )
    report = asyncio.run(service.build_report(datetime(2026, 10, 15, 12, 0, tzinfo=MSK)))

    values = {(row.exchange, row.symbol): row.amount_usd for row in report.rows}
    assert values[("Hyperliquid", "NBIS")] == Decimal("1.05")
    assert values[("Hyperliquid", "PLATINUM")] == Decimal("2.00")
    assert any("Lighter" in warning and "полный период" in warning for warning in report.warnings)


def test_lighter_uses_cumulative_deltas_only_after_full_window_coverage(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReportService

    service = FundingReportService(
        build_store(tmp_path / "credentials.json"),
        tmp_path / "funding.json",
        max_lighter_sample_gap_sec=100_000,
    )
    window_start = datetime(2026, 10, 14, 11, 30, tzinfo=MSK)
    service.record_lighter_sample(
        {"lighter:main|XPT": Decimal("10.00"), "lighter:main|XPD": Decimal("20.00")},
        now=window_start,
    )
    service.record_lighter_sample(
        {"lighter:main|XPT": Decimal("11.25"), "lighter:main|XPD": Decimal("19.50")},
        now=datetime(2026, 10, 15, 11, 5, tzinfo=MSK),
    )
    service.record_lighter_sample(
        {"lighter:main|XPT": Decimal("11.25"), "lighter:main|XPD": Decimal("19.50")},
        now=datetime(2026, 10, 15, 11, 30, tzinfo=MSK),
    )

    rows, warning = service.lighter_rows_for_window(
        window_start,
        datetime(2026, 10, 15, 11, 30, tzinfo=MSK),
    )

    values = {row.symbol: row.amount_usd for row in rows}
    assert values == {"XPD": Decimal("-0.50"), "XPT": Decimal("1.25")}
    assert warning is None


def test_lighter_omits_partial_amounts_until_full_window_is_covered(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReportService

    service = FundingReportService(build_store(tmp_path / "credentials.json"), tmp_path / "funding.json")
    service.record_lighter_sample(
        {"lighter:main|XPT": Decimal("10.00")},
        now=datetime(2026, 10, 14, 17, 0, tzinfo=MSK),
    )
    service.record_lighter_sample(
        {"lighter:main|XPT": Decimal("11.00")},
        now=datetime(2026, 10, 15, 11, 5, tzinfo=MSK),
    )

    rows, warning = service.lighter_rows_for_window(
        datetime(2026, 10, 14, 11, 30, tzinfo=MSK),
        datetime(2026, 10, 15, 11, 30, tzinfo=MSK),
    )

    assert rows == []
    assert warning is not None
    assert "полный период" in warning


def test_render_funding_report_uses_requested_exchange_coin_amount_format(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReport, FundingRow, render_funding_report

    report = FundingReport(
        start=datetime(2026, 10, 14, 11, 30, tzinfo=MSK),
        end=datetime(2026, 10, 15, 11, 30, tzinfo=MSK),
        rows=[
            FundingRow("Hyperliquid", "NBIS", Decimal("12.345")),
            FundingRow("Lighter", "XPT", Decimal("-0.5")),
        ],
        warnings=[],
    )

    text = render_funding_report(report)

    assert "Фандинг за день" in text
    assert "14.10.2026 11:30 — 15.10.2026 11:30 МСК" in text
    assert "Hyperliquid — NBIS — +12.35 USD" in text
    assert "Lighter — XPT — -0.50 USD" in text
    assert "Итого — +11.85 USD" in text


def test_send_due_funding_report_sends_once_and_marks_only_success(tmp_path: Path) -> None:
    from src.bot.run import send_due_funding_reports
    from src.services.funding_report_service import FundingReportService

    async def request_json(method: str, url: str, **kwargs):
        if method == "POST":
            return [{"time": 1, "delta": {"coin": "io:NBIS", "usdc": "1.00"}}]
        raise AssertionError("unexpected request")

    service = FundingReportService(
        build_store(tmp_path / "credentials.json", with_lighter=False),
        tmp_path / "funding.json",
        request_json=request_json,
    )
    bot = AsyncMock()
    now = datetime(2026, 10, 15, 11, 31, tzinfo=MSK)

    asyncio.run(send_due_funding_reports(bot, service, ["123"], now=now))
    asyncio.run(send_due_funding_reports(bot, service, ["123"], now=now))

    assert bot.send_message.await_count == 1
    assert service.was_sent("123", datetime(2026, 10, 15, 11, 30, tzinfo=MSK)) is True


def test_funding_schedule_never_sends_before_1130_msk(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReportService

    service = FundingReportService(build_store(tmp_path / "credentials.json", with_lighter=False), tmp_path / "funding.json")

    assert service.should_send("123", datetime(2026, 10, 15, 11, 29, tzinfo=MSK)) is False
    assert service.should_send("123", datetime(2026, 10, 15, 11, 30, tzinfo=MSK)) is True
    service.mark_sent("123", datetime(2026, 10, 15, 11, 30, tzinfo=MSK))
    assert service.should_send("123", datetime(2026, 10, 15, 11, 31, tzinfo=MSK)) is False


def test_funding_subscribers_use_daily_reports_not_alerts() -> None:
    from src.bot.run import resolve_funding_report_chat_ids

    class Preferences:
        def list_daily_report_chat_ids(self):
            return ["daily"]

        def list_alert_chat_ids(self):
            return ["alerts"]

    assert resolve_funding_report_chat_ids(Preferences(), "bootstrap") == ["bootstrap", "daily"]


def test_hyperliquid_paginates_and_rejects_malformed_amounts(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReportService

    calls = 0

    async def request_json(method: str, url: str, **kwargs):
        nonlocal calls
        calls += 1
        start = kwargs["json"]["startTime"]
        if calls == 1:
            return [
                {"time": start + index, "delta": {"coin": "io:NBIS", "usdc": "0.01"}}
                for index in range(500)
            ]
        return [{"time": start + 1, "delta": {"coin": "io:NBIS", "usdc": "bad-number"}}]

    service = FundingReportService(
        build_store(tmp_path / "credentials.json", with_lighter=False),
        tmp_path / "funding.json",
        request_json=request_json,
    )
    report = asyncio.run(service.build_report(datetime(2026, 10, 15, 12, 0, tzinfo=MSK)))

    assert calls == 2
    assert report.complete is False
    assert report.rows == []
    assert any("Hyperliquid" in warning for warning in report.warnings)


def test_lighter_gap_or_stale_end_makes_window_incomplete(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReportService

    service = FundingReportService(build_store(tmp_path / "credentials.json"), tmp_path / "funding.json")
    start = datetime(2026, 10, 14, 11, 30, tzinfo=MSK)
    end = datetime(2026, 10, 15, 11, 30, tzinfo=MSK)
    service.record_lighter_sample({"lighter:main|XPT": Decimal("10")}, now=start)
    service.record_lighter_failure(datetime(2026, 10, 15, 11, 29, tzinfo=MSK))
    service.record_lighter_sample({"lighter:main|XPT": Decimal("11")}, now=end)

    rows, warning = service.lighter_rows_for_window(start, end)

    assert rows == []
    assert warning is not None
    assert "неполные данные" in warning


def test_lighter_flat_position_keeps_cumulative_funding_baseline(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReportService

    totals = iter(["10.00", "11.25"])

    async def request_json(method: str, url: str, **kwargs):
        return {
            "accounts": [{"positions": [{"symbol": "XPT", "position": "0", "total_funding_paid_out": next(totals)}]}]
        }

    service = FundingReportService(
        build_store(tmp_path / "credentials.json"),
        tmp_path / "funding.json",
        max_lighter_sample_gap_sec=100_000,
        request_json=request_json,
    )
    start = datetime(2026, 10, 14, 11, 30, tzinfo=MSK)
    asyncio.run(service.capture_lighter_sample(start))
    asyncio.run(service.capture_lighter_sample(datetime(2026, 10, 15, 11, 30, tzinfo=MSK)))

    rows, warning = service.lighter_rows_for_window(start, datetime(2026, 10, 15, 11, 30, tzinfo=MSK))
    assert warning is None
    assert {row.symbol: row.amount_usd for row in rows} == {"XPT": Decimal("1.25")}


def test_incomplete_report_is_not_sent_or_marked(tmp_path: Path) -> None:
    from src.bot.run import send_due_funding_reports
    from src.services.funding_report_service import FundingReportService

    async def request_json(method: str, url: str, **kwargs):
        raise RuntimeError("venue unavailable")

    service = FundingReportService(
        build_store(tmp_path / "credentials.json", with_lighter=False),
        tmp_path / "funding.json",
        request_json=request_json,
    )
    bot = AsyncMock()
    now = datetime(2026, 10, 15, 11, 31, tzinfo=MSK)

    asyncio.run(send_due_funding_reports(bot, service, ["123"], now=now))

    assert bot.send_message.await_count == 0
    assert service.was_sent("123", datetime(2026, 10, 15, 11, 30, tzinfo=MSK)) is False


def test_non_finite_hyperliquid_amount_makes_report_incomplete(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReportService

    async def request_json(method: str, url: str, **kwargs):
        start = kwargs["json"]["startTime"]
        return [{"time": start + 1, "delta": {"coin": "io:NBIS", "usdc": "NaN"}}]

    service = FundingReportService(
        build_store(tmp_path / "credentials.json", with_lighter=False),
        tmp_path / "funding.json",
        request_json=request_json,
    )
    report = asyncio.run(service.build_report(datetime(2026, 10, 15, 12, 0, tzinfo=MSK)))

    assert report.complete is False
    assert report.rows == []


def test_blank_enabled_hyperliquid_address_makes_report_incomplete(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReportService

    store = build_store(tmp_path / "credentials.json", with_lighter=False)
    store.set_exchange_credentials("hyperliquid:blank", {"user_address": " "})

    async def request_json(method: str, url: str, **kwargs):
        return []

    service = FundingReportService(store, tmp_path / "funding.json", request_json=request_json)
    report = asyncio.run(service.build_report(datetime(2026, 10, 15, 12, 0, tzinfo=MSK)))

    assert report.complete is False
    assert any("hyperliquid:blank" in warning for warning in report.warnings)


def test_sent_window_dedupe_keeps_all_current_recipients(tmp_path: Path) -> None:
    from src.services.funding_report_service import FundingReportService

    service = FundingReportService(build_store(tmp_path / "credentials.json", with_lighter=False), tmp_path / "funding.json")
    window_end = datetime(2026, 10, 15, 11, 30, tzinfo=MSK)
    for chat_index in range(401):
        service.mark_sent(str(chat_index), window_end)

    assert service.was_sent("0", window_end) is True
    assert service.was_sent("400", window_end) is True
