from __future__ import annotations

from pathlib import Path


def test_start_setup_requires_admin(tmp_path: Path) -> None:
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.credential_store import CredentialStore
    from src.services.telegram_preferences import TelegramPreferencesService

    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001"]),
        credential_store=CredentialStore(tmp_path / "credentials.json"),
    )

    reply = flow.start_setup(chat_id="999")

    assert "доступ" in reply.lower()


def test_setup_flow_collects_fields_and_stores_masked_credentials(tmp_path: Path) -> None:
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.credential_store import CredentialStore
    from src.services.telegram_preferences import TelegramPreferencesService

    store = CredentialStore(tmp_path / "credentials.json")
    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001"]),
        credential_store=store,
    )

    start_reply = flow.start_setup(chat_id="1001", command_text="/setup aden")
    next_reply = flow.handle_message(chat_id="1001", text="api-key-123456")
    final_reply = flow.handle_message(chat_id="1001", text="secret-987654")
    exchanges_reply = flow.list_exchanges(chat_id="1001")

    assert "api_key" in start_reply
    assert "api_secret" in next_reply
    assert "сохранены" in final_reply.lower()
    assert "проверка пройдена" not in final_reply.lower()
    assert "проверка учетных данных не выполнялась" in final_reply.lower()
    assert store.get_exchange_credentials("aden") == {
        "api_key": "api-key-123456",
        "api_secret": "secret-987654",
    }
    assert store.is_exchange_enabled("aden") is True
    assert "aden: ON, configured" in exchanges_reply
    assert "[REDACTED]" not in exchanges_reply
    assert "secr...7654" in exchanges_reply
    assert "secret-987654" not in exchanges_reply


def test_cancel_clears_active_setup_session(tmp_path: Path) -> None:
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.credential_store import CredentialStore
    from src.services.telegram_preferences import TelegramPreferencesService

    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001"]),
        credential_store=CredentialStore(tmp_path / "credentials.json"),
    )
    flow.start_setup(chat_id="1001")
    flow.handle_message(chat_id="1001", text="okx")

    cancel_reply = flow.cancel(chat_id="1001")
    after_cancel_reply = flow.handle_message(chat_id="1001", text="ignored")

    assert "отмен" in cancel_reply.lower()
    assert "актив" in after_cancel_reply.lower()


def test_remove_exchange_without_argument_prompts_to_pick_saved_profile(tmp_path: Path) -> None:
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.credential_store import CredentialStore
    from src.services.telegram_preferences import TelegramPreferencesService

    store = CredentialStore(tmp_path / "credentials.json")
    store.set_exchange_credentials("hyperliquid:hl1", {"user_address": "0xabc", "private_key": "secret", "dex": "main"})
    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001"]),
        credential_store=store,
    )

    reply = flow.remove_exchange(chat_id="1001", command_text="/remove_exchange")

    assert "выберите профиль" in reply.lower()
    assert "hyperliquid:hl1" in reply


def test_remove_exchange_command_deletes_saved_credentials(tmp_path: Path) -> None:
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.credential_store import CredentialStore
    from src.services.telegram_preferences import TelegramPreferencesService

    store = CredentialStore(tmp_path / "credentials.json")
    store.set_exchange_credentials("okx", {"api_key": "key12345", "api_secret": "secret12345", "api_passphrase": "pass12345"})
    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001"]),
        credential_store=store,
    )

    reply = flow.remove_exchange(chat_id="1001", command_text="/remove_exchange okx")

    assert "удалена" in reply.lower()
    assert store.get_exchange_credentials("okx") == {}
    assert store.is_exchange_enabled("okx") is False


def test_remove_exchange_without_saved_profiles_returns_clear_message(tmp_path: Path) -> None:
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.credential_store import CredentialStore
    from src.services.telegram_preferences import TelegramPreferencesService

    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001"]),
        credential_store=CredentialStore(tmp_path / "credentials.json"),
    )

    reply = flow.remove_exchange(chat_id="1001", command_text="/remove_exchange")

    assert "сохраненных профилей" in reply.lower()


def test_enable_and_disable_exchange_commands_update_store(tmp_path: Path) -> None:
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.credential_store import CredentialStore
    from src.services.telegram_preferences import TelegramPreferencesService

    store = CredentialStore(tmp_path / "credentials.json")
    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001"]),
        credential_store=store,
    )

    enable_reply = flow.enable_exchange(chat_id="1001", command_text="/enable_exchange bingx")
    disable_reply = flow.disable_exchange(chat_id="1001", command_text="/disable_exchange bingx")

    assert "ещё не добавлены" in enable_reply
    assert "выключена" in disable_reply.lower()
    assert store.is_exchange_enabled("bingx") is False


def test_select_exchange_starts_profile_collection_without_text_command(tmp_path: Path) -> None:
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.credential_store import CredentialStore
    from src.services.telegram_preferences import TelegramPreferencesService

    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001"]),
        credential_store=CredentialStore(tmp_path / "credentials.json"),
    )

    reply = flow.select_exchange(chat_id="1001", exchange="okx")

    assert flow.has_active_session("1001") is True
    assert "имя профиля" in reply.lower()


def test_select_exchange_rejects_unknown_exchange(tmp_path: Path) -> None:
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.credential_store import CredentialStore
    from src.services.telegram_preferences import TelegramPreferencesService

    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001"]),
        credential_store=CredentialStore(tmp_path / "credentials.json"),
    )

    reply = flow.select_exchange(chat_id="1001", exchange="unknown")

    assert "неизвестная биржа" in reply.lower()


def test_setup_flow_collects_profile_name_before_credentials(tmp_path: Path) -> None:
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.credential_store import CredentialStore
    from src.services.telegram_preferences import TelegramPreferencesService

    store = CredentialStore(tmp_path / "credentials.json")
    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001"]),
        credential_store=store,
    )

    start_reply = flow.select_exchange(chat_id="1001", exchange="hyperliquid")
    next_reply = flow.handle_message(chat_id="1001", text="main")
    final_reply = flow.handle_message(chat_id="1001", text="0xabc")

    assert "имя профиля" in start_reply.lower()
    assert "wallet address" in next_reply.lower()
    assert "api wallet address" not in next_reply.lower()
    assert "private key" not in next_reply.lower()
    assert "dex" not in next_reply.lower()
    assert "сохранены" in final_reply.lower()
    assert "проверка учетных данных не выполнялась" in final_reply.lower()
    assert store.get_exchange_credentials("hyperliquid:main") == {"user_address": "0xabc"}


def test_setup_flow_accepts_exchange_and_profile_in_command(tmp_path: Path) -> None:
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.credential_store import CredentialStore
    from src.services.telegram_preferences import TelegramPreferencesService

    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(state_path=tmp_path / "state.json", admin_chat_ids=["1001"]),
        credential_store=CredentialStore(tmp_path / "credentials.json"),
    )

    reply = flow.start_setup(chat_id="1001", command_text="/setup hyperliquid main")

    assert "wallet address" in reply.lower()
    assert "api wallet address" not in reply.lower()
    assert "private key" not in reply.lower()
    assert "dex" not in reply.lower()
