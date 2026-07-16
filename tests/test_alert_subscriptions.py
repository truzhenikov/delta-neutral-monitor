from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock

from src.core.schemas import StatusOut
from src.services.alerting import AlertingService
from src.services.credential_store import CredentialStore
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
                    "positions": [
                        {
                            "exchange": "extended",
                            "symbol": "BTCUSDT",
                            "side": "long",
                            "size": 0.1,
                            "entry_price": 103.0,
                            "mark_price": 100.0,
                            "leverage": 5.0,
                            "liquidation_price": 90.32,
                            "notional_usd": 5000.0,
                            "pnl_usd": 100.0,
                            "delta_usd": -50.0,
                        }
                    ],
                }
            ],
            "connector_statuses": [
                {"exchange": "extended", "ok": False, "error": "timeout", "updated_at": now}
            ],
            "risk": {
                "net_delta_usd": -52.98,
                "margin_ratio": 0.155,
                "min_liq_distance_pct": 9.68,
                "risk_level": "medium",
                "warnings": ["Min liquidation distance 9.68% <= threshold 12.00% for extended BTCUSDT (long)"],
                "generated_at": now,
            },
            "current_snapshot": {
                "recorded_at": now,
                "total_equity_usd": 42126.34,
                "total_available_margin_usd": 16172.42,
                "total_maintenance_margin_usd": 6536.58,
                "warning_count": 1,
                "warnings": ["Min liquidation distance 9.68% <= threshold 12.00% for extended BTCUSDT (long)"],
            },
            "source": "stale",
            "snapshot_updated_at": now,
        }
    )


def make_test_jwt(*, exp: datetime, address: str = "0x5ffa22c26988633e4d8aa4e5fc78a0d1f76f44c3") -> str:
    header = {"typ": "JWT", "alg": "HS256"}
    payload = {
        "address": address,
        "auth_at": int((exp - timedelta(days=7)).timestamp()),
        "iat": int((exp - timedelta(days=7)).timestamp()),
        "exp": int(exp.timestamp()),
    }

    def encode(part: dict) -> str:
        raw = json.dumps(part, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    return f"{encode(header)}.{encode(payload)}.signature"


def test_list_alert_chat_ids_includes_bootstrap_chat_when_enabled(tmp_path: Path) -> None:
    from src.bot.run import resolve_alert_chat_ids

    prefs = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])
    prefs.set_alerts_enabled("111", True)
    prefs.set_alerts_enabled("222", False)

    assert resolve_alert_chat_ids(prefs, bootstrap_chat_id="999") == ["111", "999"]


def test_list_alert_chat_ids_excludes_unsubscribed_chats(tmp_path: Path) -> None:
    from src.bot.run import resolve_alert_chat_ids

    prefs = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])
    prefs.set_alerts_enabled("111", True)
    prefs.set_alerts_enabled("222", False)

    assert resolve_alert_chat_ids(prefs, bootstrap_chat_id="") == ["111"]


def test_cooldown_is_global_across_broadcasted_chats(tmp_path: Path) -> None:
    from src.bot.run import send_alerts_for_snapshot

    bot = AsyncMock()
    prefs = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])
    prefs.set_alerts_enabled("111", True)
    prefs.set_alerts_enabled("222", True)
    alerting = AlertingService(cooldown_sec=300)
    status = build_status()

    asyncio.run(send_alerts_for_snapshot(bot, status, prefs, alerting, bootstrap_chat_id=""))
    asyncio.run(send_alerts_for_snapshot(bot, status, prefs, alerting, bootstrap_chat_id=""))

    sent_messages = [kwargs for _, kwargs in bot.send_message.await_args_list]
    assert len(sent_messages) == 4
    assert {item["chat_id"] for item in sent_messages} == {"111", "222"}


def test_send_alerts_for_snapshot_swallows_send_failures(tmp_path: Path) -> None:
    from src.bot.run import send_alerts_for_snapshot

    bot = AsyncMock()
    bot.send_message.side_effect = TimeoutError("telegram timeout")
    prefs = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])
    prefs.set_alerts_enabled("111", True)
    alerting = AlertingService(cooldown_sec=300)
    status = build_status()

    asyncio.run(send_alerts_for_snapshot(bot, status, prefs, alerting, bootstrap_chat_id=""))

    assert bot.send_message.await_count > 0


def test_failed_alert_delivery_does_not_consume_cooldown(tmp_path: Path) -> None:
    from src.bot.run import send_alerts_for_snapshot

    bot = AsyncMock()
    bot.send_message.side_effect = [
        TimeoutError("telegram timeout"),
        TimeoutError("telegram timeout"),
        None,
        None,
    ]
    prefs = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])
    prefs.set_alerts_enabled("111", True)
    alerting = AlertingService(cooldown_sec=300)
    status = build_status()

    asyncio.run(send_alerts_for_snapshot(bot, status, prefs, alerting, bootstrap_chat_id=""))
    asyncio.run(send_alerts_for_snapshot(bot, status, prefs, alerting, bootstrap_chat_id=""))

    sent_messages = [kwargs for _, kwargs in bot.send_message.await_args_list]
    assert len(sent_messages) == 4


def test_send_alerts_for_snapshot_respects_chat_specific_liq_threshold(tmp_path: Path) -> None:
    from src.bot.run import send_alerts_for_snapshot

    bot = AsyncMock()
    prefs = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])
    prefs.set_alerts_enabled("111", True)
    prefs.set_alerts_enabled("222", True)
    prefs.set_alert_min_liq_distance_pct("111", 8.0)
    prefs.set_alert_min_liq_distance_pct("222", 10.0)
    alerting = AlertingService(cooldown_sec=300)
    status = build_status()

    asyncio.run(send_alerts_for_snapshot(bot, status, prefs, alerting, bootstrap_chat_id=""))

    sent_messages = [kwargs for _, kwargs in bot.send_message.await_args_list]
    liq_messages = [item for item in sent_messages if "Min liquidation distance" in item["text"]]
    connector_messages = [item for item in sent_messages if item["text"].startswith("CONNECTOR ALERT")]

    assert {item["chat_id"] for item in liq_messages} == {"222"}
    assert len(connector_messages) == 2
    assert {item["chat_id"] for item in connector_messages} == {"111", "222"}


def test_collect_pending_token_expiry_alerts_emits_expiring_and_expired_once(tmp_path: Path) -> None:
    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    store = CredentialStore(storage_path=tmp_path / "credentials.json")
    store.set_exchange_credentials(
        "variational:main",
        {"vr_token": make_test_jwt(exp=now + timedelta(hours=12))},
    )
    alerting = AlertingService(cooldown_sec=300)

    expiring = alerting.collect_pending_token_expiry_alerts(
        store,
        now=now,
        warning_window=timedelta(hours=24),
    )
    assert len(expiring) == 1
    assert expiring[0].key.startswith("token:variational:main:expiring:")
    assert "TOKEN EXPIRING [variational:main]" in expiring[0].text

    alerting.mark_sent(expiring[0].key, sent_at=now)
    assert alerting.collect_pending_token_expiry_alerts(
        store,
        now=now + timedelta(hours=1),
        warning_window=timedelta(hours=24),
    ) == []

    expired = alerting.collect_pending_token_expiry_alerts(
        store,
        now=now + timedelta(hours=13),
        warning_window=timedelta(hours=24),
    )
    assert len(expired) == 1
    assert expired[0].key.startswith("token:variational:main:expired:")
    assert "TOKEN EXPIRED [variational:main]" in expired[0].text


def test_send_alerts_for_snapshot_includes_variational_token_expiry_alert(tmp_path: Path) -> None:
    from src.bot.run import send_alerts_for_snapshot

    now = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    store = CredentialStore(storage_path=tmp_path / "credentials.json")
    store.set_exchange_credentials(
        "variational:main",
        {"vr_token": make_test_jwt(exp=now + timedelta(hours=6))},
    )

    bot = AsyncMock()
    prefs = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])
    prefs.set_alerts_enabled("111", True)
    alerting = AlertingService(cooldown_sec=300)
    status = build_status()

    asyncio.run(
        send_alerts_for_snapshot(
            bot,
            status,
            prefs,
            alerting,
            bootstrap_chat_id="",
            credential_store=store,
            now=now,
        )
    )

    texts = [kwargs["text"] for _, kwargs in bot.send_message.await_args_list]
    assert any(text.startswith("TOKEN EXPIRING [variational:main]") for text in texts)
