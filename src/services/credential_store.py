from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CredentialStore:
    SUPPORTED_EXCHANGES: dict[str, tuple[str, ...]] = {
        "bitget": ("api_key", "api_secret", "api_passphrase"),
        "bingx": ("api_key", "api_secret"),
        "okx": ("api_key", "api_secret", "api_passphrase"),
        "kucoin": ("api_key", "api_secret", "api_passphrase"),
        "aden": ("api_key", "api_secret"),
        "hyperliquid": ("user_address",),
        "extended": ("api_key", "stark_key_public", "stark_key_private", "vault_number", "client_id"),
    }
    PROFILE_SEPARATOR = ":"

    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path

    @classmethod
    def normalize_exchange_ref(cls, exchange: str) -> str:
        normalized_exchange = exchange.strip().lower()
        if not normalized_exchange:
            raise ValueError("Unsupported exchange: empty")
        if cls.PROFILE_SEPARATOR not in normalized_exchange:
            if normalized_exchange not in cls.SUPPORTED_EXCHANGES:
                raise ValueError(f"Unsupported exchange: {exchange}")
            return normalized_exchange
        base_exchange, profile_name = normalized_exchange.split(cls.PROFILE_SEPARATOR, 1)
        if base_exchange not in cls.SUPPORTED_EXCHANGES or not profile_name.strip():
            raise ValueError(f"Unsupported exchange: {exchange}")
        return f"{base_exchange}{cls.PROFILE_SEPARATOR}{profile_name.strip()}"

    @classmethod
    def get_base_exchange(cls, exchange: str) -> str:
        normalized_exchange = cls.normalize_exchange_ref(exchange)
        return normalized_exchange.split(cls.PROFILE_SEPARATOR, 1)[0]

    @classmethod
    def get_profile_name(cls, exchange: str) -> str | None:
        normalized_exchange = cls.normalize_exchange_ref(exchange)
        if cls.PROFILE_SEPARATOR not in normalized_exchange:
            return None
        return normalized_exchange.split(cls.PROFILE_SEPARATOR, 1)[1]

    @classmethod
    def format_exchange_ref(cls, exchange: str, profile_name: str | None = None) -> str:
        normalized_exchange = cls.get_base_exchange(exchange)
        if profile_name is None:
            return normalized_exchange
        normalized_profile = cls.normalize_profile_name(profile_name)
        return f"{normalized_exchange}{cls.PROFILE_SEPARATOR}{normalized_profile}"

    @classmethod
    def normalize_profile_name(cls, profile_name: str) -> str:
        normalized_profile = profile_name.strip().lower().replace(" ", "-")
        if not normalized_profile:
            raise ValueError("Profile name is empty")
        if cls.PROFILE_SEPARATOR in normalized_profile:
            raise ValueError("Profile name must not contain ':'")
        return normalized_profile

    def set_exchange_credentials(self, exchange: str, payload: dict[str, str]) -> None:
        normalized_exchange = self.normalize_exchange_ref(exchange)
        base_exchange = self.get_base_exchange(normalized_exchange)
        allowed_fields = set(self.SUPPORTED_EXCHANGES[base_exchange])
        filtered_payload = {key: value for key, value in payload.items() if key in allowed_fields and value}
        state = self._read_state()
        current = state["exchanges"].get(
            normalized_exchange,
            {
                "base_exchange": base_exchange,
                "profile_name": self.get_profile_name(normalized_exchange),
                "enabled": False,
                "credentials": {},
            },
        )
        state["exchanges"][normalized_exchange] = {
            "base_exchange": base_exchange,
            "profile_name": self.get_profile_name(normalized_exchange),
            "enabled": True,
            "credentials": filtered_payload,
        }
        if current.get("enabled") is False and not filtered_payload:
            state["exchanges"][normalized_exchange]["enabled"] = False
        self._write_state(state)

    def get_exchange_credentials(self, exchange: str) -> dict[str, str]:
        normalized_exchange = self.normalize_exchange_ref(exchange)
        entry = self._read_state()["exchanges"].get(normalized_exchange, {})
        return dict(entry.get("credentials", {}))

    def get_exchange_credentials_masked(self, exchange: str) -> dict[str, str]:
        return {
            key: self._display_value(key, value)
            for key, value in self.get_exchange_credentials(exchange).items()
        }

    def set_exchange_enabled(self, exchange: str, enabled: bool) -> None:
        normalized_exchange = self.normalize_exchange_ref(exchange)
        base_exchange = self.get_base_exchange(normalized_exchange)
        state = self._read_state()
        current = state["exchanges"].get(normalized_exchange, {"credentials": {}})
        state["exchanges"][normalized_exchange] = {
            "base_exchange": base_exchange,
            "profile_name": self.get_profile_name(normalized_exchange),
            "enabled": bool(enabled),
            "credentials": dict(current.get("credentials", {})),
        }
        self._write_state(state)

    def is_exchange_enabled(self, exchange: str) -> bool:
        normalized_exchange = self.normalize_exchange_ref(exchange)
        entry = self._read_state()["exchanges"].get(normalized_exchange)
        return bool(entry and entry.get("enabled"))

    def remove_exchange(self, exchange: str) -> None:
        normalized_exchange = self.normalize_exchange_ref(exchange)
        state = self._read_state()
        state["exchanges"].pop(normalized_exchange, None)
        self._write_state(state)

    def list_configured_exchanges(self) -> list[dict[str, Any]]:
        state = self._read_state()
        items: list[dict[str, Any]] = []
        for exchange in sorted(state["exchanges"]):
            entry = state["exchanges"][exchange]
            credentials = entry.get("credentials", {})
            if not credentials:
                continue
            items.append(
                {
                    "exchange": exchange,
                    "base_exchange": entry.get("base_exchange", self.get_base_exchange(exchange)),
                    "profile_name": entry.get("profile_name"),
                    "enabled": bool(entry.get("enabled")),
                    "credentials": {key: self._display_value(key, value) for key, value in credentials.items()},
                }
            )
        return items

    def list_exchange_statuses(self) -> list[dict[str, Any]]:
        state = self._read_state()
        items: list[dict[str, Any]] = []
        seen_exchanges: set[str] = set()

        for exchange in sorted(state["exchanges"]):
            entry = state["exchanges"][exchange]
            credentials = entry.get("credentials", {})
            items.append(
                {
                    "exchange": exchange,
                    "base_exchange": entry.get("base_exchange", self.get_base_exchange(exchange)),
                    "profile_name": entry.get("profile_name"),
                    "enabled": bool(entry.get("enabled")),
                    "configured": bool(credentials),
                    "credentials": {key: self._display_value(key, value) for key, value in credentials.items()},
                }
            )
            seen_exchanges.add(exchange)

        for exchange in sorted(self.SUPPORTED_EXCHANGES):
            if exchange in seen_exchanges:
                continue
            items.append(
                {
                    "exchange": exchange,
                    "base_exchange": exchange,
                    "profile_name": None,
                    "enabled": False,
                    "configured": False,
                    "credentials": {},
                }
            )
        return items

    def list_enabled_exchanges(self, default: list[str] | None = None) -> list[str]:
        state = self._read_state()
        if not state["exchanges"] and not self.storage_path.exists():
            return list(default or [])
        return [
            exchange
            for exchange in sorted(state["exchanges"])
            if bool(state["exchanges"][exchange].get("enabled"))
        ]

    def get_state_revision(self) -> int:
        if not self.storage_path.exists():
            return 0
        return self.storage_path.stat().st_mtime_ns

    def _normalize_exchange(self, exchange: str) -> str:
        return self.normalize_exchange_ref(exchange)

    def _read_state(self) -> dict[str, dict[str, dict[str, Any]]]:
        if not self.storage_path.exists():
            return {"exchanges": {}}
        payload = json.loads(self.storage_path.read_text(encoding="utf-8"))
        return self._normalize_state(payload)

    def _normalize_state(self, payload: Any) -> dict[str, dict[str, dict[str, Any]]]:
        if not isinstance(payload, dict):
            return {"exchanges": {}}

        raw_entries = payload.get("exchanges") if isinstance(payload.get("exchanges"), dict) else payload
        normalized_entries: dict[str, dict[str, Any]] = {}

        for raw_exchange, raw_entry in raw_entries.items():
            if not isinstance(raw_exchange, str):
                continue
            try:
                normalized_exchange = self.normalize_exchange_ref(raw_exchange)
            except ValueError:
                continue
            base_exchange = self.get_base_exchange(normalized_exchange)
            profile_name = self.get_profile_name(normalized_exchange)
            if isinstance(raw_entry, dict) and "credentials" in raw_entry:
                raw_credentials = raw_entry.get("credentials") or {}
                raw_enabled = raw_entry.get("enabled", False)
                raw_base_exchange = raw_entry.get("base_exchange") or base_exchange
                raw_profile_name = raw_entry.get("profile_name", profile_name)
            else:
                raw_credentials = raw_entry or {}
                raw_enabled = True
                raw_base_exchange = base_exchange
                raw_profile_name = profile_name
            if not isinstance(raw_credentials, dict):
                raw_credentials = {}
            try:
                normalized_base_exchange = self.get_base_exchange(str(raw_base_exchange))
            except ValueError:
                normalized_base_exchange = base_exchange
            normalized_profile_name = raw_profile_name if raw_profile_name is None else str(raw_profile_name).strip().lower()
            allowed_fields = set(self.SUPPORTED_EXCHANGES[normalized_base_exchange])
            credentials = {
                key: str(value)
                for key, value in raw_credentials.items()
                if key in allowed_fields and isinstance(value, str) and value
            }
            normalized_entries[normalized_exchange] = {
                "base_exchange": normalized_base_exchange,
                "profile_name": normalized_profile_name,
                "enabled": bool(raw_enabled),
                "credentials": credentials,
            }

        return {"exchanges": normalized_entries}

    def _write_state(self, payload: dict[str, dict[str, dict[str, Any]]]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self.storage_path)

    def _display_value(self, key: str, value: str) -> str:
        if key == "dex":
            return value
        return self._mask_value(value)

    def _mask_value(self, value: str) -> str:
        if len(value) <= 8:
            return "*" * len(value)
        prefix = value[:4].rstrip("-_ ")
        suffix = value[-4:]
        return f"{prefix}...{suffix}"
