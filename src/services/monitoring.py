from __future__ import annotations

import asyncio
import logging

from src.connectors.base import ExchangeConnector
from src.core.models import AccountSnapshot, ConnectorStatus, utc_now

logger = logging.getLogger(__name__)


class MonitoringService:
    def __init__(self, connectors: list[ExchangeConnector]) -> None:
        self.connectors = connectors

    async def collect(self) -> list[AccountSnapshot]:
        accounts, _ = await self.collect_with_status()
        return accounts

    async def collect_with_status(self) -> tuple[list[AccountSnapshot], list[ConnectorStatus]]:
        if not self.connectors:
            return [], []
        results = await asyncio.gather(
            *(c.fetch_account_snapshot() for c in self.connectors), return_exceptions=True
        )

        accounts: list[AccountSnapshot] = []
        statuses: list[ConnectorStatus] = []
        for idx, result in enumerate(results):
            exchange = getattr(self.connectors[idx], "exchange", f"connector_{idx}")
            if isinstance(result, Exception):
                logger.warning("connector_failed exchange=%s error=%s", exchange, result)
                statuses.append(
                    ConnectorStatus(
                        exchange=exchange,
                        ok=False,
                        error=str(result),
                        updated_at=utc_now(),
                    )
                )
                continue
            accounts.append(result)
            statuses.append(
                ConnectorStatus(
                    exchange=exchange,
                    ok=True,
                    error=None,
                    updated_at=utc_now(),
                )
            )
        return accounts, statuses
