from __future__ import annotations

import json
from pathlib import Path


class CredentialStore:
    SUPPORTED_EXCHANGES: dict[str, tuple[str, ...]] = {
        "bitget": ("api_key", "api_secret", "api_passphrase"),
        "bingx": ("api_key", "api_secret"),
        "okx": ("api_key", "api_secret", "api_passphrase"),
        "kucoin": ("api_key", "api_secret", "api_passphrase"),
        "aden": ("api_key", "api_secret"),
        "hyperliquid": ("user_address", "private_key", "dex"),
        "extended": ("api_key", "stark_key_public", "stark_key_private", "vault_number", "client_id"),
    }

    def __init__(self, storage_path: Path) -> None:
        self.storage_path = storage_path

    def set_exchange_credentials(self, exchange: str, payload: dict[str, str]) -> None:
        normalized_exchange = self._normalize_exchange(exchange)
        allowed_fields = set(self.SUPPORTED_EXCHANGES[normalized_exchange])
        filtered_payload = {key: value for key, value in payload.items() if key in allowed_fields and value}
        state = self._read_state()
        state[normalized_exchange] = filtered_payload
        self._write_state(state)

    def get_exchange_credentials(self, exchange: str) -> dict[str, str]:
        normalized_exchange = self._normalize_exchange(exchange)
        return dict(self._read_state().get(normalized_exchange, {}))

    def get_exchange_credentials_masked(self, exchange: str) -> dict[str, str]:
        return {key: self._mask_value(value) for key, value in self.get_exchange_credentials(exchange).items()}

    def remove_exchange(self, exchange: str) -> None:
        normalized_exchange = self._normalize_exchange(exchange)
        state = self._read_state()
        state.pop(normalized_exchange, None)
        self._write_state(state)

    def list_configured_exchanges(self) -> list[dict[str, dict[str, str] | str]]:
        state = self._read_state()
        return [
            {
                "exchange": exchange,
                "credentials": {key: self._mask_value(value) for key, value in state[exchange].items()},
            }
            for exchange in sorted(state)
        ]

    def _normalize_exchange(self, exchange: str) -> str:
        normalized_exchange = exchange.strip().lower()
        if normalized_exchange not in self.SUPPORTED_EXCHANGES:
            raise ValueError(f"Unsupported exchange: {exchange}")
        return normalized_exchange

    def _read_state(self) -> dict[str, dict[str, str]]:
        if not self.storage_path.exists():
            return {}
        return json.loads(self.storage_path.read_text(encoding="utf-8"))

    def _write_state(self, payload: dict[str, dict[str, str]]) -> None:
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.storage_path.with_suffix(self.storage_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self.storage_path)

    def _mask_value(self, value: str) -> str:
        if len(value) <= 8:
            return "*" * len(value)
        prefix = value[:4].rstrip("-_ ")
        suffix = value[-4:]
        return f"{prefix}...{suffix}"
