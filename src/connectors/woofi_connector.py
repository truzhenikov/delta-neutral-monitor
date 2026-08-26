from __future__ import annotations

import base64
from datetime import datetime, timezone
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.config import get_settings
from src.connectors.base import ExchangeConnector
from src.connectors.real_connectors import (
    RealConnectorNotConfiguredError,
    RealConnectorRequestError,
    _BaseRealConnector,
    _runtime_credentials,
    _safe_float,
    _safe_liq_price,
)
from src.core.models import AccountSnapshot, Position, utc_now

_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _base58_decode(value: str) -> bytes:
    number = 0
    for char in value.strip():
        try:
            number = number * 58 + _BASE58_ALPHABET.index(char)
        except ValueError as exc:
            raise ValueError("WOOFi Orderly secret must be base58 encoded") from exc
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    return (b"\x00" * (len(value) - len(value.lstrip("1")))) + raw


class WoofiRealConnector(_BaseRealConnector):
    """Read-only WOOFi Pro connector using the Orderly REST API."""

    exchange = "woofi"

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        account_id = (credentials.get("account_id") or settings.woofi_account_id).strip()
        orderly_key = (credentials.get("orderly_key") or settings.woofi_orderly_key).strip()
        secret = (credentials.get("orderly_secret") or settings.woofi_orderly_secret).strip()
        if not (account_id and orderly_key and secret):
            raise RealConnectorNotConfiguredError(
                "woofi credentials are not configured (WOOFI_ACCOUNT_ID/WOOFI_ORDERLY_KEY/WOOFI_ORDERLY_SECRET)"
            )
        timestamp = str(int(datetime.now(timezone.utc).timestamp() * 1000))
        private_key = Ed25519PrivateKey.from_private_bytes(_base58_decode(secret))
        message = f"{timestamp}{method.upper()}{path}".encode("utf-8")
        signature = private_key.sign(message)
        public_key = orderly_key if orderly_key.startswith("ed25519:") else f"ed25519:{orderly_key}"
        return {
            "Content-Type": "application/x-www-form-urlencoded",
            "orderly-account-id": account_id,
            "orderly-key": public_key,
            "orderly-timestamp": timestamp,
            "orderly-signature": base64.urlsafe_b64encode(signature).decode("ascii").rstrip("="),
        }

    @staticmethod
    def _data(payload: dict[str, Any], context: str) -> Any:
        if not isinstance(payload, dict) or payload.get("success") is False:
            raise RealConnectorRequestError(f"woofi {context} error: {payload}")
        return payload.get("data") or {}

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        account = self._data(
            await self._get(settings.woofi_api_base, "/v1/account_info", headers=self._auth_headers("GET", "/v1/account_info")),
            "account",
        )
        raw_positions = self._data(
            await self._get(settings.woofi_api_base, "/v1/positions", headers=self._auth_headers("GET", "/v1/positions")),
            "positions",
        )
        if isinstance(raw_positions, dict):
            raw_positions = raw_positions.get("rows") or raw_positions.get("positions") or []

        positions: list[Position] = []
        for row in raw_positions or []:
            quantity = _safe_float(row.get("position_qty", row.get("quantity", row.get("qty", 0))))
            if quantity == 0:
                continue
            side_raw = str(row.get("position_side", row.get("side", ""))).lower()
            side = "short" if side_raw in {"short", "sell"} or quantity < 0 else "long"
            mark = _safe_float(row.get("mark_price", row.get("markPrice", row.get("mark", 0))))
            entry = _safe_float(row.get("average_open_price", row.get("averageOpenPrice", row.get("entry_price", mark))), default=mark)
            leverage = _safe_float(row.get("leverage"), default=1.0)
            positions.append(Position(exchange=self.exchange, symbol=str(row.get("symbol") or "UNKNOWN"), side=side, size=abs(quantity), entry_price=entry, mark_price=mark, leverage=leverage if leverage > 0 else 1.0, liquidation_price=_safe_liq_price(row.get("est_liq_price", row.get("liquidation_price")))))

        equity = _safe_float(account.get("account_value", account.get("total_collateral_value")))
        ratio = _safe_float(account.get("maintenance_margin_ratio"))
        maintenance = equity * ratio if ratio > 0 else _safe_float(account.get("maintenance_margin"))
        return AccountSnapshot(exchange=self.exchange, equity_usd=equity, available_margin_usd=_safe_float(account.get("free_collateral", account.get("available"))), maintenance_margin_usd=maintenance, positions=positions, updated_at=utc_now())
