from __future__ import annotations

from src.connectors.base import ExchangeConnector
from src.connectors.mock_connectors import (
    AdenConnector,
    BingxConnector,
    BitgetConnector,
    ExtendedConnector,
    GateConnector,
    HyperliquidConnector,
    KucoinConnector,
    LighterConnector,
    MexcConnector,
    OkxConnector,
    VestConnector,
)
from src.connectors.real_connectors import (
    AdenRealConnector,
    BingxRealConnector,
    BitgetRealConnector,
    ExtendedRealConnector,
    GateRealConnector,
    HyperliquidRealConnector,
    KucoinRealConnector,
    LighterRealConnector,
    MexcRealConnector,
    OkxRealConnector,
    VestRealConnector,
)
from src.services.credential_store import CredentialStore

MOCK_CONNECTOR_MAP: dict[str, type[ExchangeConnector]] = {
    "aden": AdenConnector,
    "bitget": BitgetConnector,
    "bingx": BingxConnector,
    "gate": GateConnector,
    "mexc": MexcConnector,
    "hyperliquid": HyperliquidConnector,
    "extended": ExtendedConnector,
    "okx": OkxConnector,
    "kucoin": KucoinConnector,
    "vest": VestConnector,
    "lighter": LighterConnector,
}

REAL_CONNECTOR_MAP: dict[str, type[ExchangeConnector]] = {
    "aden": AdenRealConnector,
    "bitget": BitgetRealConnector,
    "bingx": BingxRealConnector,
    "gate": GateRealConnector,
    "mexc": MexcRealConnector,
    "hyperliquid": HyperliquidRealConnector,
    "extended": ExtendedRealConnector,
    "okx": OkxRealConnector,
    "kucoin": KucoinRealConnector,
    "vest": VestRealConnector,
    "lighter": LighterRealConnector,
}


def build_connectors(exchanges: list[str], use_mock_data: bool = False) -> list[ExchangeConnector]:
    connector_map = MOCK_CONNECTOR_MAP if use_mock_data else REAL_CONNECTOR_MAP
    connectors: list[ExchangeConnector] = []
    for exchange_ref in exchanges:
        try:
            base_exchange = CredentialStore.get_base_exchange(exchange_ref)
        except ValueError:
            continue
        if base_exchange not in connector_map:
            continue
        connector = connector_map[base_exchange]()
        connector.exchange = CredentialStore.normalize_exchange_ref(exchange_ref)
        connectors.append(connector)
    return connectors
