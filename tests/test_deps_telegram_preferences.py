from __future__ import annotations

from src.deps import get_credential_store, get_telegram_preferences_service


def test_get_telegram_preferences_service_uses_configured_state_path(monkeypatch, tmp_path) -> None:
    from src.config import get_settings

    state_path = tmp_path / "telegram-state.json"
    get_settings.cache_clear()
    get_telegram_preferences_service.cache_clear()
    monkeypatch.setenv("TELEGRAM_STATE_PATH", str(state_path))
    monkeypatch.setenv("TELEGRAM_ADMIN_CHAT_IDS", "111,222")
    monkeypatch.setenv("TELEGRAM_DAILY_REPORT_HOUR_UTC", "9")

    service = get_telegram_preferences_service()
    second = get_telegram_preferences_service()

    assert service is second
    assert service.state_path == state_path
    assert service.is_admin("111") is True
    assert service.is_admin("333") is False
    assert service.get_chat("999")["daily_report_hour_utc"] == 9

    get_telegram_preferences_service.cache_clear()
    get_settings.cache_clear()


def test_get_credential_store_uses_configured_storage_path(monkeypatch, tmp_path) -> None:
    from src.config import get_settings

    storage_path = tmp_path / "exchange-credentials.json"
    get_settings.cache_clear()
    get_credential_store.cache_clear()
    monkeypatch.setenv("CREDENTIAL_STORE_PATH", str(storage_path))

    store = get_credential_store()
    second = get_credential_store()

    assert store is second
    assert store.storage_path == storage_path

    get_credential_store.cache_clear()
    get_settings.cache_clear()
