from __future__ import annotations

from src.bot.rendering import (
    render_alert_settings_text,
    render_daily_report_text,
    render_daily_snapshots_text,
    render_portfolio_text,
)
from src.services.telegram_preferences import TelegramPreferencesService


def build_portfolio_reply(status: dict) -> str:
    return render_portfolio_text(status)


def build_daily_reply(history: dict, status: dict | None = None) -> str:
    daily_changes = history.get("daily_changes") or []
    if not daily_changes:
        return render_daily_report_text(
            {"date": "n/a", "equity_usd": 0.0, "warning_count": 0},
            None,
            status,
        )

    current = daily_changes[0]
    previous = daily_changes[1] if len(daily_changes) > 1 else None
    return render_daily_report_text(current, previous, status)


def build_daily_snapshots_reply(history: dict) -> str:
    daily_changes = history.get("daily_changes") or []
    return render_daily_snapshots_text(daily_changes)


def build_alert_settings_reply(preferences: TelegramPreferencesService, chat_id: str) -> str:
    return render_alert_settings_text(preferences.get_chat(chat_id))


def toggle_alerts(preferences: TelegramPreferencesService, chat_id: str, enabled: bool) -> str:
    preferences.set_alerts_enabled(chat_id, enabled)
    return build_alert_settings_reply(preferences, chat_id)


def toggle_daily_reports(preferences: TelegramPreferencesService, chat_id: str, enabled: bool) -> str:
    preferences.set_daily_report_enabled(chat_id, enabled)
    return build_alert_settings_reply(preferences, chat_id)


def set_alert_min_liq_distance(preferences: TelegramPreferencesService, chat_id: str, value: float) -> str:
    preferences.set_alert_min_liq_distance_pct(chat_id, value)
    return build_alert_settings_reply(preferences, chat_id)
