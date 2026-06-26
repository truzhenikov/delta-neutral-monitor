from __future__ import annotations

from src.connectors.base import ExchangeConnector
from src.connectors.mock_data import make_mock_snapshot
from src.core.models import AccountSnapshot


class BitgetConnector(ExchangeConnector):
    exchange = "bitget"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=0)


class BingxConnector(ExchangeConnector):
    exchange = "bingx"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=-400)


class GateConnector(ExchangeConnector):
    exchange = "gate"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=310)


class PacificaConnector(ExchangeConnector):
    exchange = "pacifica"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=185)


class MexcConnector(ExchangeConnector):
    exchange = "mexc"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=350)


class HyperliquidConnector(ExchangeConnector):
    exchange = "hyperliquid"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=620)


class ExtendedConnector(ExchangeConnector):
    exchange = "extended"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=-180)


class OkxConnector(ExchangeConnector):
    exchange = "okx"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=220)


class KucoinConnector(ExchangeConnector):
    exchange = "kucoin"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=140)


class AdenConnector(ExchangeConnector):
    exchange = "aden"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=-90)


class VestConnector(ExchangeConnector):
    exchange = "vest"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=75)


class LighterConnector(ExchangeConnector):
    exchange = "lighter"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=95)
