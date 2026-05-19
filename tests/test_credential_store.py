from __future__ import annotations

from pathlib import Path

import pytest


def test_set_and_get_exchange_credentials_round_trip(tmp_path: Path) -> None:
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "exchanges.json")

    store.set_exchange_credentials("aden", {"api_key": "akey1234", "api_secret": "secret456"})

    assert store.get_exchange_credentials("aden") == {"api_key": "akey1234", "api_secret": "secret456"}


def test_get_exchange_credentials_masked_hides_secrets(tmp_path: Path) -> None:
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "exchanges.json")
    store.set_exchange_credentials(
        "okx",
        {
            "api_key": "okxk12345678",
            "api_secret": "okx-secret-abcdef",
            "api_passphrase": "pass-7890",
        },
    )

    masked = store.get_exchange_credentials_masked("okx")

    assert masked == {
        "api_key": "okxk...5678",
        "api_secret": "okx...cdef",
        "api_passphrase": "pass...7890",
    }


def test_remove_exchange_deletes_saved_credentials(tmp_path: Path) -> None:
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "exchanges.json")
    store.set_exchange_credentials("extended", {"api_key": "extd1234"})

    store.remove_exchange("extended")

    assert store.get_exchange_credentials("extended") == {}
    assert store.list_configured_exchanges() == []


def test_unsupported_exchange_is_rejected(tmp_path: Path) -> None:
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "exchanges.json")

    with pytest.raises(ValueError, match="Unsupported exchange"):
        store.set_exchange_credentials("binance", {"api_key": "bnce1234"})


def test_list_configured_exchanges_returns_masked_payloads(tmp_path: Path) -> None:
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "exchanges.json")
    store.set_exchange_credentials("aden", {"api_key": "hello1234", "api_secret": "secret67890"})
    store.set_exchange_credentials("hyperliquid", {"user_address": "0x1234567890abcdef"})

    assert store.list_configured_exchanges() == [
        {
            "exchange": "aden",
            "credentials": {"api_key": "hell...1234", "api_secret": "secr...7890"},
        },
        {
            "exchange": "hyperliquid",
            "credentials": {"user_address": "0x12...cdef"},
        },
    ]
