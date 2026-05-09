from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.models import AccountSnapshot


class ExchangeConnector(ABC):
    exchange: str

    @abstractmethod
    async def fetch_account_snapshot(self) -> AccountSnapshot:
        raise NotImplementedError
