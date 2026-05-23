from __future__ import annotations

from src.services.telegram_preferences import TelegramPreferencesService


def sample_status() -> dict:
    return {
        "total_equity_usd": 42126.34,
        "total_available_margin_usd": 16172.42,
        "total_maintenance_margin_usd": 6536.58,
        "accounts": [
            {"exchange": "hyperliquid", "equity_usd": 9552.39, "positions": []},
            {"exchange": "extended", "equity_usd": 8046.41, "positions": []},
        ],
        "connector_statuses": [
            {"exchange": "hyperliquid", "ok": True, "error": None},
            {"exchange": "extended", "ok": False, "error": "timeout"},
        ],
        "risk": {
            "net_delta_usd": -52.98,
            "margin_ratio": 0.155,
            "min_liq_distance_pct": 9.68,
            "risk_level": "medium",
            "warnings": ["Min liquidation distance 9.68% <= threshold 12.00%"],
        },
    }


def sample_history() -> dict:
    return {
        "daily_changes": [
            {
                "date": "2026-05-18",
                "equity_usd": 42126.34,
                "change_usd": 826.34,
                "warning_count": 1,
                "warnings": ["liq warning"],
            },
            {
                "date": "2026-05-17",
                "equity_usd": 41300.00,
                "change_usd": -50.0,
                "warning_count": 0,
                "warnings": [],
            },
        ]
    }


def test_build_portfolio_reply_uses_renderer_output() -> None:
    from src.bot.command_logic import build_portfolio_reply

    text = build_portfolio_reply(sample_status())

    assert "Portfolio summary" in text
    assert "42,126.34 USD" in text
    assert "Connectors down: extended" in text


def test_build_daily_reply_uses_latest_two_days() -> None:
    from src.bot.command_logic import build_daily_reply

    text = build_daily_reply(sample_history())

    assert "Daily portfolio report" in text
    assert "2026-05-18" in text
    assert "41,300.00 USD" in text
    assert "+826.34 USD" in text


def test_build_daily_reply_includes_copy_block_as_monospace_table() -> None:
    from src.bot.command_logic import build_daily_reply

    status = {
        "accounts": [
            {"exchange": "hyperliquid", "equity_usd": 9490.24},
            {"exchange": "okx", "equity_usd": 8517.0},
            {"exchange": "extended", "equity_usd": 8009.5},
            {"exchange": "bingx", "equity_usd": 6466.1},
            {"exchange": "bitget", "equity_usd": 4318.27},
            {"exchange": "aden", "equity_usd": 2843.17},
            {"exchange": "kucoin", "equity_usd": 2454.33},
        ]
    }

    text = build_daily_reply(sample_history(), status)

    assert "<pre>" in text
    assert "</pre>" in text
    assert "```" not in text
    assert "Hyperliquid  Okx      Extended  Bingx    Bitget   Aden     Kucoin   Total" in text
    assert "9490.24      8517.00  8009.50   6466.10  4318.27  2843.17  2454.33  42098.61" in text
    assert "HL\tAden\tKucoin" not in text
    assert "12189,72" not in text


def test_build_daily_reply_handles_missing_previous_day() -> None:
    from src.bot.command_logic import build_daily_reply

    text = build_daily_reply({"daily_changes": [{"date": "2026-05-18", "equity_usd": 42126.34, "change_usd": None, "warning_count": 0, "warnings": []}]})

    assert "No previous day snapshot yet" in text


def test_toggle_alerts_updates_preferences_and_returns_settings_text(tmp_path) -> None:
    from src.bot.command_logic import toggle_alerts

    prefs = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])

    text = toggle_alerts(prefs, chat_id="777", enabled=True)

    assert prefs.get_chat("777")["alerts_enabled"] is True
    assert "Liquidation/risk alerts: ON" in text


def test_toggle_daily_reports_updates_preferences_and_returns_settings_text(tmp_path) -> None:
    from src.bot.command_logic import toggle_daily_reports

    prefs = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[], daily_report_hour_utc=9)

    text = toggle_daily_reports(prefs, chat_id="777", enabled=True)

    assert prefs.get_chat("777")["daily_report_enabled"] is True
    assert "Daily report: ON" in text
    assert "Scheduled hour (UTC): 09:00" in text
