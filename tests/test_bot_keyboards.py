from __future__ import annotations

from pathlib import Path


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
    store.set_exchange_credentials("hyperliquid:hl1", {"user_address": "0xabc", "private_key": "secret", "dex": "main"})
    store.set_exchange_credentials("okx:main", {"api_key": "key", "api_secret": "secret", "api_passphrase": "pass"})

    markup = build_remove_exchange_keyboard(store)

    buttons = [button for row in markup.inline_keyboard for button in row]
    texts = [button.text for button in buttons]
    callback_data = [button.callback_data for button in buttons]

    assert texts == ["hyperliquid:hl1", "okx:main", "Отмена"]
    assert callback_data == ["remove_exchange:hyperliquid:hl1", "remove_exchange:okx:main", "remove_exchange:cancel"]
