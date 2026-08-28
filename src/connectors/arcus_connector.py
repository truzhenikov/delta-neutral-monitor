from __future__ import annotations

from typing import Any

from src.config import get_settings
from src.connectors.real_connectors import (
    RealConnectorNotConfiguredError,
    RealConnectorRequestError,
    _BaseRealConnector,
    _runtime_credentials,
    _safe_float,
    _safe_liq_price,
)
from src.core.models import AccountSnapshot, Position, utc_now


class ArcusRealConnector(_BaseRealConnector):
    """Read-only Arcus connector using its public address-scoped REST API."""

    exchange = "arcus"

    @staticmethod
    def _value(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
        for key in keys:
            if key in row and row[key] is not None:
                return row[key]
        return default

    @classmethod
    def _position_rows(cls, payload: Any) -> list[tuple[str, dict[str, Any]]]:
        if not isinstance(payload, dict):
            raise RealConnectorRequestError(f"arcus positions error: {payload}")
        positions = payload.get("positions")
        if not isinstance(positions, dict):
            return []
        rows: list[tuple[str, dict[str, Any]]] = []
        for market_id, row in positions.items():
            if isinstance(row, dict):
                rows.append((str(market_id), row))
        return rows

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        settings = get_settings()
        credentials = _runtime_credentials(self.exchange)
        address = (credentials.get("address") or settings.arcus_address).strip().lower()
        account_index = (credentials.get("account_index") or str(settings.arcus_account_index)).strip()
        if not address:
            raise RealConnectorNotConfiguredError("arcus address is not configured (ARCUS_ADDRESS)")
        try:
            index = int(account_index)
        except (TypeError, ValueError) as exc:
            raise RealConnectorNotConfiguredError("arcus account index must be an integer from 0 to 9") from exc
        if index < 0 or index > 9:
            raise RealConnectorNotConfiguredError("arcus account index must be an integer from 0 to 9")

        params = {"address": address, "accountIndex": index}
        account = await self._get(settings.arcus_api_base, "/v1/account", params=params)
        positions_payload = await self._get(settings.arcus_api_base, "/v1/positions", params=params)
        if not isinstance(account, dict):
            raise RealConnectorRequestError(f"arcus account error: {account}")
        if "error" in account:
            raise RealConnectorRequestError(f"arcus account error: {account}")

        positions: list[Position] = []
        for market_id, row in self._position_rows(positions_payload):
            raw_size = _safe_float(self._value(row, "size", "positionSize", "quantity", "qty"))
            if raw_size == 0:
                continue
            side_raw = str(self._value(row, "side", "direction", default="")).strip().lower()
            side = "short" if raw_size < 0 or side_raw in {"short", "sell"} else "long"
            size = abs(raw_size)
            mark = _safe_float(self._value(row, "markPrice", "oraclePrice", "price"))
            entry = _safe_float(self._value(row, "avgEntryPrice", "averageEntryPrice", "entryPrice"), default=mark)
            symbol = str(self._value(row, "market", "marketName", "symbol", default=market_id))
            positions.append(Position(
                exchange=self.exchange,
                symbol=symbol,
                side=side,
                size=size,
                entry_price=entry,
                mark_price=mark,
                leverage=_safe_float(self._value(row, "leverage"), default=1.0),
                liquidation_price=_safe_liq_price(self._value(row, "liquidationPrice", "liquidation_price", "estLiqPrice")),
            ))

        return AccountSnapshot(
            exchange=self.exchange,
            equity_usd=_safe_float(account.get("equity")),
            available_margin_usd=_safe_float(account.get("freeCollateral")),
            maintenance_margin_usd=_safe_float(account.get("maintenanceMargin", account.get("maintenance_margin"))),
            positions=positions,
            updated_at=utc_now(),
        )
