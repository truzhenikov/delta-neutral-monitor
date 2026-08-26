from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from src.config import get_settings
from src.connectors.real_connectors import _BaseRealConnector, _runtime_credentials, _safe_float, _safe_liq_price, RealConnectorNotConfiguredError, RealConnectorRequestError
from src.core.models import AccountSnapshot, Position, utc_now


class AsterRealConnector(_BaseRealConnector):
    """Read-only Aster Futures connector using the documented Binance-compatible API."""

    exchange = "aster"

    def _signed_params(self) -> tuple[dict[str, Any], dict[str, str]]:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        api_key = (credentials.get("api_key") or settings.aster_api_key).strip()
        api_secret = (credentials.get("api_secret") or settings.aster_api_secret).strip()
        if not (api_key and api_secret):
            raise RealConnectorNotConfiguredError("aster credentials are not configured (ASTER_API_KEY/ASTER_API_SECRET)")
        params: dict[str, Any] = {"recvWindow": 5000, "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000)}
        query = urlencode(params)
        params["signature"] = hmac.new(api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return params, {"X-MBX-APIKEY": api_key, "Accept": "application/json"}

    @staticmethod
    def _data(payload: Any, context: str) -> Any:
        if not isinstance(payload, (dict, list)):
            raise RealConnectorRequestError(f"aster {context} error: {payload}")
        if isinstance(payload, dict) and "code" in payload and payload.get("code", 0) < 0:
            raise RealConnectorRequestError(f"aster {context} error: {payload}")
        return payload

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        account_params, headers = self._signed_params()
        account = self._data(await self._get(settings.aster_api_base, "/fapi/v4/account", params=account_params, headers=headers), "account")
        position_params, position_headers = self._signed_params()
        raw_positions = self._data(await self._get(settings.aster_api_base, "/fapi/v2/positionRisk", params=position_params, headers=position_headers), "positions")
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
