from __future__ import annotations

from src.connectors.base import ExchangeConnector
from src.connectors.mock_connectors import (
    AdenConnector,
    BingxConnector,
    BitgetConnector,
    ExtendedConnector,
    HyperliquidConnector,
    KucoinConnector,
    MexcConnector,
    OkxConnector,
)
from src.connectors.real_connectors import (
    AdenRealConnector,
    BingxRealConnector,
    BitgetRealConnector,
    ExtendedRealConnector,
    HyperliquidRealConnector,
    KucoinRealConnector,
    MexcRealConnector,
    OkxRealConnector,
)

MOCK_CONNECTOR_MAP: dict[str, type[ExchangeConnector]] = {
    "aden": AdenConnector,
    "bitget": BitgetConnector,
    "bingx": BingxConnector,
    "mexc": MexcConnector,
    "hyperliquid": HyperliquidConnector,
    "extended": ExtendedConnector,
    "okx": OkxConnector,
    "kucoin": KucoinConnector,
}

REAL_CONNECTOR_MAP: dict[str, type[ExchangeConnector]] = {
    "aden": AdenRealConnector,
    "bitget": BitgetRealConnector,
    "bingx": BingxRealConnector,
    "mexc": MexcRealConnector,
    "hyperliquid": HyperliquidRealConnector,
    "extended": ExtendedRealConnector,
    "okx": OkxRealConnector,
    "kucoin": KucoinRealConnector,
}


def build_connectors(exchanges: list[str], use_mock_data: bool = False) -> list[ExchangeConnector]:
    connector_map = MOCK_CONNECTOR_MAP if use_mock_data else REAL_CONNECTOR_MAP
    connectors: list[ExchangeConnector] = []
    for exch in exchanges:
        if exch not in connector_map:
            continue
        connectors.append(connector_map[exch]())
    return connectors
