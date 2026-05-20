from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

from src.config import get_settings
from src.connectors.base import ExchangeConnector
from src.core.models import AccountSnapshot, ConnectorStatus, Position, utc_now

logger = logging.getLogger(__name__)


class MonitoringService:
    def __init__(self, connectors: list[ExchangeConnector], cache_path: Path | None = None) -> None:
        self.connectors = connectors
        self.cache_path = cache_path

    async def _fetch_with_retry(self, connector: ExchangeConnector) -> AccountSnapshot:
        try:
            return await connector.fetch_account_snapshot()
        except Exception as exc:
            if "timestamp" not in str(exc).lower():
                raise
            exchange = getattr(connector, "exchange", connector.__class__.__name__)
            logger.warning("connector_timestamp_retry exchange=%s error=%s", exchange, exc)
            return await connector.fetch_account_snapshot()

    async def collect(self) -> list[AccountSnapshot]:
        accounts, _ = await self.collect_with_status()
        return accounts

    async def collect_with_status(self) -> tuple[list[AccountSnapshot], list[ConnectorStatus]]:
        if not self.connectors:
            return [], []
        timeout_sec = get_settings().request_timeout_sec
        results = await asyncio.gather(
            *(asyncio.wait_for(self._fetch_with_retry(c), timeout=timeout_sec) for c in self.connectors),
            return_exceptions=True,
        )

        cached_accounts = self._read_cached_accounts()
        # Keep the latest per-exchange snapshot on disk so connector outages degrade to
        # stale data instead of wiping the exchange out of the portfolio view.
        accounts_by_exchange: dict[str, AccountSnapshot] = {account.exchange: account for account in cached_accounts}
        statuses: list[ConnectorStatus] = []
        live_accounts_seen = False
        for idx, result in enumerate(results):
            exchange = getattr(self.connectors[idx], "exchange", f"connector_{idx}")
            if isinstance(result, Exception):
                logger.warning("connector_failed exchange=%s error=%s", exchange, result)
                if exchange in accounts_by_exchange:
                    logger.info("connector_reusing_cached_snapshot exchange=%s", exchange)
                error_text = str(result) or result.__class__.__name__.replace("Error", "").lower()
                statuses.append(
                    ConnectorStatus(
                        exchange=exchange,
                        ok=False,
                        error=error_text,
                        updated_at=utc_now(),
                    )
                )
                continue
            live_accounts_seen = True
            accounts_by_exchange[exchange] = result
            statuses.append(
                ConnectorStatus(
                    exchange=exchange,
                    ok=True,
                    error=None,
                    updated_at=utc_now(),
                )
            )

        accounts = [accounts_by_exchange[status.exchange] for status in statuses if status.exchange in accounts_by_exchange]
        if live_accounts_seen:
            self._write_cached_accounts(accounts)
        return accounts, statuses

    def _read_cached_accounts(self) -> list[AccountSnapshot]:
        if self.cache_path is None or not self.cache_path.exists():
            return []
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
            return [self._account_from_dict(item) for item in payload]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("connector_cache_read_failed path=%s error=%s", self.cache_path, exc)
            return []

    def _write_cached_accounts(self, accounts: list[AccountSnapshot]) -> None:
        if self.cache_path is None:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = [self._account_to_dict(account) for account in accounts]
        self.cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _account_to_dict(self, account: AccountSnapshot) -> dict:
        return {
            "exchange": account.exchange,
            "equity_usd": account.equity_usd,
            "available_margin_usd": account.available_margin_usd,
            "maintenance_margin_usd": account.maintenance_margin_usd,
            "updated_at": account.updated_at.isoformat(),
            "positions": [
                {
                    "exchange": position.exchange,
                    "symbol": position.symbol,
                    "side": position.side,
                    "size": position.size,
                    "entry_price": position.entry_price,
                    "mark_price": position.mark_price,
                    "leverage": position.leverage,
                    "liquidation_price": position.liquidation_price,
                }
                for position in account.positions
            ],
        }

    def _account_from_dict(self, payload: dict) -> AccountSnapshot:
        return AccountSnapshot(
            exchange=payload["exchange"],
            equity_usd=payload["equity_usd"],
            available_margin_usd=payload["available_margin_usd"],
            maintenance_margin_usd=payload["maintenance_margin_usd"],
            updated_at=datetime.fromisoformat(payload["updated_at"]),
            positions=[
                Position(
                    exchange=position["exchange"],
                    symbol=position["symbol"],
                    side=position["side"],
                    size=position["size"],
                    entry_price=position["entry_price"],
                    mark_price=position["mark_price"],
                    leverage=position["leverage"],
                    liquidation_price=position["liquidation_price"],
                )
                for position in payload.get("positions", [])
            ],
        )
