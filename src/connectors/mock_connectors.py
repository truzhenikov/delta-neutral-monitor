from __future__ import annotations

from src.connectors.base import ExchangeConnector
from src.connectors.mock_data import make_mock_snapshot
from src.core.models import AccountSnapshot


class BinanceConnector(ExchangeConnector):
    exchange = "binance"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=280)


class BitgetConnector(ExchangeConnector):
    exchange = "bitget"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=0)


class BingxConnector(ExchangeConnector):
    exchange = "bingx"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=-400)


class PhemexConnector(ExchangeConnector):
    exchange = "phemex"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=-260)


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


class TxflowConnector(ExchangeConnector):
    exchange = "txflow"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=410)


class OndoConnector(ExchangeConnector):
    exchange = "ondo"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=155)


class RisexConnector(ExchangeConnector):
    exchange = "risex"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=205)


class VariationalConnector(ExchangeConnector):
    exchange = "variational"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=265)


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


class LighterRHConnector(ExchangeConnector):
    exchange = "lighter-rh"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=105)


class NadoConnector(ExchangeConnector):
    exchange = "nado"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=115)


class AsterConnector(ExchangeConnector):
    exchange = "aster"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=135)


class WoofiConnector(ExchangeConnector):
    exchange = "woofi"

    async def fetch_account_snapshot(self) -> AccountSnapshot:
        return make_mock_snapshot(self.exchange, price_shift=125)
