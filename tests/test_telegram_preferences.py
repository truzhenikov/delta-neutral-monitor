from __future__ import annotations

import json
from pathlib import Path

from src.services.telegram_preferences import TelegramPreferencesService


def test_preferences_store_creates_file_and_persists_flags(tmp_path: Path) -> None:
    service = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])

    updated = service.set_alerts_enabled("123", True)
    updated = service.set_daily_report_enabled("123", False)

    assert updated["alerts_enabled"] is True
    assert updated["daily_report_enabled"] is False
    payload = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert payload["chats"]["123"]["alerts_enabled"] is True
    assert payload["chats"]["123"]["daily_report_enabled"] is False


def test_preferences_store_persists_between_service_instances(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    TelegramPreferencesService(state_path=path, admin_chat_ids=[]).set_alerts_enabled("321", True)

    reloaded = TelegramPreferencesService(state_path=path, admin_chat_ids=[])

    assert reloaded.get_chat("321")["alerts_enabled"] is True


def test_preferences_store_returns_defaults_for_unknown_chat(tmp_path: Path) -> None:
    service = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=[])

    chat = service.get_chat("999")

    assert chat["alerts_enabled"] is False
    assert chat["daily_report_enabled"] is False
    assert chat["alert_min_liq_distance_pct"] == 12.0
    assert chat["authorized"] is False
    assert chat["last_daily_report_date"] is None


def test_settings_parses_admin_chat_ids(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_IDS", "1001, 1002,1003")

    settings = get_settings()

    assert settings.telegram_admin_chat_ids_list == ["1001", "1002", "1003"]
    get_settings.cache_clear()


def test_preferences_store_checks_admin_allowlist(tmp_path: Path) -> None:
    service = TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001", "1002"])

    assert service.is_admin("1001") is True
    assert service.is_admin("2001") is False
