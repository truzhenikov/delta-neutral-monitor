from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.core.schemas import StatusOut
from src.services.alerting import AlertingService
from src.services.telegram_preferences import TelegramPreferencesService


def build_status() -> StatusOut:
    now = datetime(2026, 5, 18, 20, 0, tzinfo=timezone.utc)
    return StatusOut.model_validate(
        {
            "total_equity_usd": 42126.34,
            "total_available_margin_usd": 16172.42,
            "total_maintenance_margin_usd": 6536.58,
            "accounts": [
                {
                    "exchange": "extended",
                    "equity_usd": 8046.41,
                    "available_margin_usd": 3000.0,
                    "maintenance_margin_usd": 1200.0,
                    "position_count": 1,
                    "total_notional_usd": 5000.0,
                    "total_pnl_usd": 100.0,
                    "total_delta_usd": -50.0,
                    "load_ratio": 0.15,
                    "updated_at": now,
                    "positions": [],
                }
            ],
            "connector_statuses": [
                {"exchange": "extended", "ok": True, "error": None, "updated_at": now}
            ],
            "risk": {
                "net_delta_usd": -52.98,
                "margin_ratio": 0.155,
                "min_liq_distance_pct": 9.68,
                "risk_level": "medium",
                "warnings": ["Min liquidation distance 9.68% <= threshold 12.00%"],
                "generated_at": now,
            },
            "current_snapshot": {
                "recorded_at": now,
                "total_equity_usd": 42126.34,
                "total_available_margin_usd": 16172.42,
                "total_maintenance_margin_usd": 6536.58,
                "warning_count": 1,
                "warnings": ["Min liquidation distance 9.68% <= threshold 12.00%"],
            },
            "source": "live",
            "snapshot_updated_at": now,
        }
    )


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _FakeAsyncClient:
    def __init__(self, *, expected_url: str, payload: dict, seen_urls: list[str]) -> None:
        self.expected_url = expected_url
        self.payload = payload
        self.seen_urls = seen_urls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, *, headers: dict[str, str]) -> _FakeResponse:
        self.seen_urls.append(url)
        assert headers == {"Accept": "application/json"}
        assert url == self.expected_url
        return _FakeResponse(self.payload)


@pytest.mark.asyncio
async def test_collect_status_snapshot_fetches_backend_status_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.bot import run

    status = build_status()
    seen_urls: list[str] = []

    monkeypatch.setattr(
        run,
        "get_settings",
        lambda: type("Settings", (), {"api_port": 8080, "request_timeout_sec": 12.5})(),
    )
    monkeypatch.setattr(
        run.httpx,
        "AsyncClient",
        lambda timeout: _FakeAsyncClient(
            expected_url="http://127.0.0.1:8080/v1/status",
            payload=status.model_dump(mode="json"),
            seen_urls=seen_urls,
        ),
    )

    snapshot = await run.collect_status_snapshot()

    assert seen_urls == ["http://127.0.0.1:8080/v1/status"]
    assert snapshot["risk"]["net_delta_usd"] == -52.98
    assert snapshot["source"] == "live"


def test_alert_loop_uses_shared_backend_snapshot_instead_of_local_monitoring(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from src.bot import run

    stop_event = asyncio.Event()
    bot = AsyncMock()
    prefs = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])
    prefs.set_alerts_enabled("111", True)
    status = build_status()

    async def fake_collect_status_snapshot() -> dict:
        stop_event.set()
        return status.model_dump(mode="json")

    monkeypatch.setattr(
        run,
        "get_settings",
        lambda: type("Settings", (), {"telegram_alert_chat_id": "", "alert_poll_interval_sec": 300})(),
    )
    monkeypatch.setattr(run, "get_telegram_preferences_service", lambda: prefs)
    monkeypatch.setattr(run, "get_alerting_service", lambda: AlertingService(cooldown_sec=0))
    monkeypatch.setattr(run, "collect_status_snapshot", fake_collect_status_snapshot)
    asyncio.run(run.alert_loop(bot, stop_event))

    sent_messages = [kwargs for _, kwargs in bot.send_message.await_args_list]
    assert len(sent_messages) == 1
    assert sent_messages[0]["chat_id"] == "111"
    assert sent_messages[0]["text"].startswith("RISK ALERT:")


def test_alert_loop_retries_after_backend_timeout(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from src.bot import run

    stop_event = asyncio.Event()
    bot = AsyncMock()
    prefs = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])
    prefs.set_alerts_enabled("111", True)
    status = build_status()
    attempts = {"count": 0}

    async def fake_collect_status_snapshot() -> dict:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TimeoutError("backend status timeout")
        stop_event.set()
        return status.model_dump(mode="json")

    async def fake_wait() -> None:
        return None

    async def fake_wait_for(awaitable, timeout):
        awaitable.close()
        return None

    monkeypatch.setattr(
        run,
        "get_settings",
        lambda: type("Settings", (), {"telegram_alert_chat_id": "", "alert_poll_interval_sec": 300})(),
    )
    monkeypatch.setattr(run, "get_telegram_preferences_service", lambda: prefs)
    monkeypatch.setattr(run, "get_alerting_service", lambda: AlertingService(cooldown_sec=0))
    monkeypatch.setattr(run, "collect_status_snapshot", fake_collect_status_snapshot)
    monkeypatch.setattr(run.asyncio, "wait_for", fake_wait_for)

    asyncio.run(run.alert_loop(bot, stop_event))

    assert attempts["count"] == 2
    sent_messages = [kwargs for _, kwargs in bot.send_message.await_args_list]
    assert len(sent_messages) == 1
    assert sent_messages[0]["chat_id"] == "111"
