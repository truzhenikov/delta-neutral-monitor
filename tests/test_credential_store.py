from __future__ import annotations

from pathlib import Path

import pytest


def test_set_and_get_exchange_credentials_round_trip(tmp_path: Path) -> None:
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "exchanges.json")

    store.set_exchange_credentials("aden", {"api_key": "akey1234", "api_secret": "secret456"})

    assert store.get_exchange_credentials("aden") == {"api_key": "akey1234", "api_secret": "secret456"}
    assert store.is_exchange_enabled("aden") is True


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


def test_list_exchange_statuses_returns_enabled_and_configured_state(tmp_path: Path) -> None:
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "exchanges.json")
    store.set_exchange_enabled("bingx", True)
    store.set_exchange_credentials("aden", {"api_key": "hello1234", "api_secret": "secret67890"})

    statuses = {item["exchange"]: item for item in store.list_exchange_statuses()}

    assert statuses["bingx"]["enabled"] is True
    assert statuses["bingx"]["configured"] is False
    assert statuses["aden"]["enabled"] is True
    assert statuses["aden"]["configured"] is True
    assert statuses["aden"]["credentials"] == {"api_key": "hell...1234", "api_secret": "secr...7890"}


def test_list_enabled_exchanges_uses_default_only_when_file_missing(tmp_path: Path) -> None:
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "exchanges.json")
    assert store.list_enabled_exchanges(default=["okx"]) == ["okx"]

    store.set_exchange_enabled("okx", False)
    assert store.list_enabled_exchanges(default=["aden"]) == []


def test_legacy_storage_is_migrated_on_read(tmp_path: Path) -> None:
    from src.services.credential_store import CredentialStore

    storage_path = tmp_path / "exchanges.json"
    storage_path.write_text('{"aden": {"api_key": "legacy-key", "api_secret": "legacy-secret"}}', encoding="utf-8")

    store = CredentialStore(storage_path)

    assert store.get_exchange_credentials("aden") == {"api_key": "legacy-key", "api_secret": "legacy-secret"}
    assert store.is_exchange_enabled("aden") is True


def test_profiled_exchange_credentials_round_trip(tmp_path: Path) -> None:
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "exchanges.json")

    store.set_exchange_credentials(
        "hyperliquid:main",
        {"user_address": "0xabc", "dex": "xyz,cash"},
    )
    store.set_exchange_credentials(
        "hyperliquid:alt",
        {"user_address": "0xdef", "dex": "cash"},
    )

    assert store.get_exchange_credentials("hyperliquid:main") == {
        "user_address": "0xabc",
    }
    assert store.get_exchange_credentials("hyperliquid:alt") == {
        "user_address": "0xdef",
    }
    assert store.list_enabled_exchanges() == ["hyperliquid:alt", "hyperliquid:main"]


def test_list_exchange_statuses_includes_profiled_entries(tmp_path: Path) -> None:
    from src.services.credential_store import CredentialStore

    store = CredentialStore(tmp_path / "exchanges.json")
    store.set_exchange_credentials(
        "hyperliquid:main",
        {"user_address": "0xabc", "dex": "xyz,cash"},
    )

    statuses = {item["exchange"]: item for item in store.list_exchange_statuses()}

    assert statuses["hyperliquid:main"]["enabled"] is True
    assert statuses["hyperliquid:main"]["configured"] is True
    assert statuses["hyperliquid:main"]["base_exchange"] == "hyperliquid"
    assert statuses["hyperliquid:main"]["profile_name"] == "main"
    assert statuses["hyperliquid:main"]["credentials"] == {"user_address": "*****"}
