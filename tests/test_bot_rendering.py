from __future__ import annotations


def sample_status() -> dict:
    return {
        "total_equity_usd": 42126.34,
        "total_available_margin_usd": 16172.42,
        "total_maintenance_margin_usd": 6536.58,
        "accounts": [
            {
                "exchange": "hyperliquid",
                "equity_usd": 9552.39,
                "available_margin_usd": 100.0,
                "maintenance_margin_usd": 1000.0,
                "position_count": 8,
                "total_notional_usd": 68731.01,
                "total_pnl_usd": -100.0,
                "total_delta_usd": 68731.01,
                "load_ratio": 0.1,
                "updated_at": "2026-05-18T19:54:57Z",
                "positions": [],
            },
            {
                "exchange": "extended",
                "equity_usd": 8046.41,
                "available_margin_usd": 4758.64,
                "maintenance_margin_usd": 1244.30,
                "position_count": 2,
                "total_notional_usd": 24883.40,
                "total_pnl_usd": -111.88,
                "total_delta_usd": 24883.40,
                "load_ratio": 0.15,
                "updated_at": "2026-05-18T19:54:57Z",
                "positions": [],
            },
            {
                "exchange": "bitget",
                "equity_usd": 2800.12,
                "available_margin_usd": 2100.0,
                "maintenance_margin_usd": 240.0,
                "position_count": 1,
                "total_notional_usd": 5200.0,
                "total_pnl_usd": 15.5,
                "total_delta_usd": 5200.0,
                "load_ratio": 0.09,
                "updated_at": "2026-05-18T19:54:57Z",
                "positions": [],
            },
            {
                "exchange": "kucoin",
                "equity_usd": 2300.34,
                "available_margin_usd": 1900.0,
                "maintenance_margin_usd": 180.0,
                "position_count": 1,
                "total_notional_usd": 4100.0,
                "total_pnl_usd": -20.0,
                "total_delta_usd": 4100.0,
                "load_ratio": 0.08,
                "updated_at": "2026-05-18T19:54:57Z",
                "positions": [],
            },
            {
                "exchange": "aden",
                "equity_usd": 1800.56,
                "available_margin_usd": 1500.0,
                "maintenance_margin_usd": 120.0,
                "position_count": 0,
                "total_notional_usd": 0.0,
                "total_pnl_usd": 0.0,
                "total_delta_usd": 0.0,
                "load_ratio": 0.0,
                "updated_at": "2026-05-18T19:54:57Z",
                "positions": [],
            },
            {
                "exchange": "bingx",
                "equity_usd": 1600.78,
                "available_margin_usd": 1200.0,
                "maintenance_margin_usd": 90.0,
                "position_count": 1,
                "total_notional_usd": 3000.0,
                "total_pnl_usd": 5.0,
                "total_delta_usd": 3000.0,
                "load_ratio": 0.06,
                "updated_at": "2026-05-18T19:54:57Z",
                "positions": [],
            },
        ],
        "connector_statuses": [
            {"exchange": "hyperliquid", "ok": True, "error": None, "updated_at": "2026-05-18T19:54:57Z"},
            {"exchange": "extended", "ok": False, "error": "timeout", "updated_at": "2026-05-18T19:54:57Z"},
        ],
        "risk": {
            "net_delta_usd": -52.98,
            "margin_ratio": 0.155,
            "min_liq_distance_pct": 9.68,
            "risk_level": "medium",
            "warnings": ["Min liquidation distance 9.68% <= threshold 12.00%"],
            "generated_at": "2026-05-18T19:54:57Z",
        },
        "current_snapshot": {
            "recorded_at": "2026-05-18T16:00:00Z",
            "total_equity_usd": 42126.34,
            "total_available_margin_usd": 16172.42,
            "total_maintenance_margin_usd": 6536.58,
            "warning_count": 1,
            "warnings": ["Min liquidation distance 9.68% <= threshold 12.00%"],
        },
    }


def test_render_portfolio_text_includes_core_totals_and_exchange_breakdown() -> None:
    from src.bot.rendering import render_portfolio_text

    text = render_portfolio_text(sample_status())

    assert "Portfolio summary" in text
    assert "42,126.34 USD" in text
    assert "16,172.42 USD" in text
    assert "6,536.58 USD" in text
    assert "-52.98 USD" in text
    assert "medium" in text
    assert "Connectors down: extended" in text
    assert "Balances by exchange:" in text
    assert "Hyperliquid: 9,552.39 USD (real lev 7.20x)" in text
    assert "Extended: 8,046.41 USD (real lev 3.09x)" in text
    assert "Bitget: 2,800.12 USD (real lev 1.86x)" in text
    assert "Kucoin: 2,300.34 USD (real lev 1.78x)" in text
    assert "Aden: 1,800.56 USD (real lev 0.00x)" in text
    assert "Bingx: 1,600.78 USD (real lev 1.87x)" in text


def test_render_daily_report_text_includes_previous_day_delta_and_percent() -> None:
    from src.bot.rendering import render_daily_report_text

    current = {"date": "2026-05-18", "equity_usd": 42126.34, "warning_count": 1}
    previous = {"date": "2026-05-17", "equity_usd": 41300.00, "warning_count": 0}

    text = render_daily_report_text(current, previous)

    assert "Daily portfolio report" in text
    assert "2026-05-18" in text
    assert "42,126.34 USD" in text
    assert "41,300.00 USD" in text
    assert "+826.34 USD" in text
    assert "+2.00%" in text


def test_render_daily_report_text_formats_copy_block_like_table_preview() -> None:
    from src.bot.rendering import render_daily_report_text

    current = {"date": "2026-05-18", "equity_usd": 42126.34, "warning_count": 1}
    previous = {"date": "2026-05-17", "equity_usd": 41300.00, "warning_count": 0}
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

    text = render_daily_report_text(current, previous, status)

    assert "```" in text
    assert "Hyperliquid  Okx      Extended  Bingx    Bitget   Aden     Kucoin   Total" in text
    assert "9490.24      8517.00  8009.50   6466.10  4318.27  2843.17  2454.33  42098.61" in text


def test_render_daily_report_text_handles_missing_previous_day() -> None:
    from src.bot.rendering import render_daily_report_text

    current = {"date": "2026-05-18", "equity_usd": 42126.34, "warning_count": 1}

    text = render_daily_report_text(current, None)

    assert "Daily portfolio report" in text
    assert "42,126.34 USD" in text
    assert "No previous day snapshot yet" in text


def test_render_alert_settings_text_shows_enabled_flags() -> None:
    from src.bot.rendering import render_alert_settings_text

    text = render_alert_settings_text(
        {
            "alerts_enabled": True,
            "daily_report_enabled": False,
            "daily_report_hour_utc": 7,
        }
    )

    assert "Alert settings" in text
    assert "Liquidation/risk alerts: ON" in text
    assert "Daily report: OFF" in text
    assert "Scheduled hour (UTC): 07:00" in text
