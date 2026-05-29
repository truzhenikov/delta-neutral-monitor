from __future__ import annotations

from pathlib import Path


def test_build_main_menu_keyboard_contains_core_and_admin_buttons() -> None:
    from src.bot.keyboards import build_main_menu_keyboard, parse_main_menu_button

    markup = build_main_menu_keyboard(include_admin=True)

    buttons = [button for row in markup.keyboard for button in row]
    texts = [button.text for button in buttons]

    assert texts == [
        "Статус",
        "Портфель",
        "Риск",
        "Позиции",
        "День",
        "Алерты",
        "Алерты ВКЛ",
        "Алерты ВЫКЛ",
        "День ВКЛ",
        "День ВЫКЛ",
        "Настроить биржу",
        "Биржи",
        "Включить биржу",
        "Выключить биржу",
        "Удалить биржу",
        "Отмена",
    ]
    assert parse_main_menu_button("Статус") == "/status"
    assert parse_main_menu_button("Настроить биржу") == "/setup"
    assert parse_main_menu_button("Отмена") == "/cancel"


def test_build_main_menu_keyboard_without_admin_buttons() -> None:
    from src.bot.keyboards import build_main_menu_keyboard

    markup = build_main_menu_keyboard(include_admin=False)

    buttons = [button for row in markup.keyboard for button in row]
    texts = [button.text for button in buttons]

    assert "Настроить биржу" not in texts
    assert "Включить биржу" not in texts
    assert "Удалить биржу" not in texts


def test_build_setup_exchange_keyboard_contains_supported_exchanges() -> None:
    from src.bot.keyboards import build_setup_exchange_keyboard
    from src.services.credential_store import CredentialStore

    markup = build_setup_exchange_keyboard()

    buttons = [button for row in markup.inline_keyboard for button in row]
    texts = [button.text for button in buttons]
    callback_data = [button.callback_data for button in buttons]

    assert texts == sorted(CredentialStore.SUPPORTED_EXCHANGES)
    assert callback_data == [f"setup_exchange:{exchange}" for exchange in sorted(CredentialStore.SUPPORTED_EXCHANGES)]


def test_build_remove_exchange_keyboard_lists_profiles_and_cancel(tmp_path: Path) -> None:
    from src.bot.keyboards import build_remove_exchange_keyboard
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "credentials.json")
    store.set_exchange_credentials("hyperliquid:hl1", {"user_address": "0xabc"})
    store.set_exchange_credentials("okx:main", {"api_key": "key", "api_secret": "secret", "api_passphrase": "pass"})

    markup = build_remove_exchange_keyboard(store)

    buttons = [button for row in markup.inline_keyboard for button in row]
    texts = [button.text for button in buttons]
    callback_data = [button.callback_data for button in buttons]

    assert texts == ["hyperliquid:hl1", "okx:main", "Отмена"]
    assert callback_data == ["remove_exchange:hyperliquid:hl1", "remove_exchange:okx:main", "remove_exchange:cancel"]


def test_build_enable_exchange_keyboard_lists_profiles_and_cancel(tmp_path: Path) -> None:
    from src.bot.keyboards import build_exchange_toggle_keyboard, parse_exchange_toggle_callback
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "credentials.json")
    store.set_exchange_credentials("hyperliquid:hl1", {"user_address": "0xabc"})
    store.set_exchange_credentials("okx:main", {"api_key": "key", "api_secret": "secret", "api_passphrase": "pass"})

    markup = build_exchange_toggle_keyboard("enable_exchange", store)

    buttons = [button for row in markup.inline_keyboard for button in row]
    texts = [button.text for button in buttons]
    callback_data = [button.callback_data for button in buttons]

    assert texts == ["hyperliquid:hl1", "okx:main", "Отмена"]
    assert callback_data == ["enable_exchange:hyperliquid:hl1", "enable_exchange:okx:main", "enable_exchange:cancel"]
    assert parse_exchange_toggle_callback("enable_exchange", "enable_exchange:okx:main") == "okx:main"
    assert parse_exchange_toggle_callback("enable_exchange", "enable_exchange:cancel") == "cancel"
    assert parse_exchange_toggle_callback("enable_exchange", "disable_exchange:okx:main") is None
