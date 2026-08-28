from __future__ import annotations

import math
import threading
import time
from typing import Any
from urllib.parse import urlencode

from eth_account import Account
from eth_account.messages import encode_typed_data

from src.config import get_settings
from src.connectors.real_connectors import (
    _BaseRealConnector,
    _runtime_credentials,
    _safe_float,
    _safe_liq_price,
    RealConnectorNotConfiguredError,
    RealConnectorRequestError,
)
from src.core.models import AccountSnapshot, Position, utc_now


class AsterRealConnector(_BaseRealConnector):
    """Read-only Aster Futures V3 connector using API Wallet/Agent signing."""

    exchange = "aster"
    _nonce_lock = threading.Lock()
    _last_nonce = 0

    @classmethod
    def _nonce(cls) -> int:
        with cls._nonce_lock:
            value = math.trunc(time.time() * 1_000_000)
            if value <= cls._last_nonce:
                value = cls._last_nonce + 1
            cls._last_nonce = value
            return value

    def _credentials(self) -> tuple[str, str, str]:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        user = (credentials.get("user_address") or settings.aster_user_address).strip()
        signer = (credentials.get("signer") or settings.aster_signer).strip()
        private_key = (credentials.get("signer_private_key") or settings.aster_signer_private_key).strip()
        if not (user and signer and private_key):
            raise RealConnectorNotConfiguredError(
                "aster V3 credentials are not configured (user_address/signer/signer_private_key)"
            )
        return user, signer, private_key

    def _signed_params(self, business_params: dict[str, Any] | None = None) -> dict[str, str]:
        user, signer, private_key = self._credentials()
        params = {str(k): str(v) for k, v in (business_params or {}).items()}
        params.update({"nonce": str(self._nonce()), "signer": signer, "user": user})
        # V3 signs the exact ASCII-key-sorted form of the request parameters.
        ordered = dict(sorted(params.items()))
        payload = urlencode(ordered)
        typed_data = {
            "types": {
                "EIP712Domain": [
                    {"name": "name", "type": "string"},
                    {"name": "version", "type": "string"},
                    {"name": "chainId", "type": "uint256"},
                    {"name": "verifyingContract", "type": "address"},
                ],
                "Message": [{"name": "msg", "type": "string"}],
            },
            "primaryType": "Message",
            "domain": {
                "name": "AsterSignTransaction",
                "version": "1",
                "chainId": 1666,
                "verifyingContract": "0x0000000000000000000000000000000000000000",
            },
            "message": {"msg": payload},
        }
        signature = Account.sign_message(
            encode_typed_data(full_message=typed_data), private_key=private_key
        ).signature.hex()
        return {**ordered, "signature": signature}

    @staticmethod
    def _data(payload: Any, context: str) -> Any:
        if not isinstance(payload, (dict, list)):
            raise RealConnectorRequestError(f"aster {context} error: {payload}")
        if isinstance(payload, dict) and "code" in payload and payload.get("code", 0) < 0:
            raise RealConnectorRequestError(f"aster {context} error: {payload}")
        return payload

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        account = self._data(
            await self._get(settings.aster_api_base, "/fapi/v3/account", params=self._signed_params()),
            "account",
        )
        raw_positions = self._data(
            await self._get(settings.aster_api_base, "/fapi/v3/positionRisk", params=self._signed_params()),
            "positions",
        )
        if not isinstance(account, dict):
            raise RealConnectorRequestError(f"aster account error: {account}")

        positions: list[Position] = []
        rows = raw_positions if isinstance(raw_positions, list) else raw_positions.get("positions", [])
        for row in rows:
            amount = _safe_float(row.get("positionAmt"))
            if amount == 0:
                continue
            side = "short" if amount < 0 or str(row.get("positionSide", "")).upper() == "SHORT" else "long"
            mark = _safe_float(row.get("markPrice"))
            entry = _safe_float(row.get("entryPrice"), default=mark)
            leverage = _safe_float(row.get("leverage"), default=1.0)
            positions.append(Position(exchange=self.exchange, symbol=str(row.get("symbol") or "UNKNOWN"), side=side, size=abs(amount), entry_price=entry, mark_price=mark, leverage=leverage if leverage > 0 else 1.0, liquidation_price=_safe_liq_price(row.get("liquidationPrice"))))

        equity = _safe_float(account.get("totalMarginBalance", account.get("totalWalletBalance")))
        available = _safe_float(account.get("availableBalance"))
        maintenance = _safe_float(account.get("totalMaintMargin"))
        return AccountSnapshot(exchange=self.exchange, equity_usd=equity, available_margin_usd=available, maintenance_margin_usd=maintenance, positions=positions, updated_at=utc_now())