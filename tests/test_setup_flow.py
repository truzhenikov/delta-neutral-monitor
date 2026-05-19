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

    start_reply = flow.start_setup(chat_id="1001")
    choose_reply = flow.handle_message(chat_id="1001", text="aden")
    next_reply = flow.handle_message(chat_id="1001", text="api-key-123456")
    final_reply = flow.handle_message(chat_id="1001", text="secret-987654")
    exchanges_reply = flow.list_exchanges(chat_id="1001")

    assert "aden" in start_reply.lower()
    assert "api_key" in choose_reply
    assert "api_secret" in next_reply
    assert "сохранен" in final_reply.lower()
    assert store.get_exchange_credentials("aden") == {
        "api_key": "api-key-123456",
        "api_secret": "secret-987654",
    }
    assert "aden" in exchanges_reply.lower()
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

    assert "удален" in reply.lower()
    assert store.get_exchange_credentials("okx") == {}
