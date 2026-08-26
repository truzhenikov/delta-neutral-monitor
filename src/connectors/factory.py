from __future__ import annotations

from src.connectors.base import ExchangeConnector
from src.connectors.mock_connectors import (
    AdenConnector,
    AsterConnector,
    BinanceConnector,
    BingxConnector,
    BitgetConnector,
    ExtendedConnector,
    GateConnector,
    HyperliquidConnector,
    KucoinConnector,
    LighterConnector,
    LighterRHConnector,
    MexcConnector,
    NadoConnector,
    OndoConnector,
    OkxConnector,
    PacificaConnector,
    PhemexConnector,
    RisexConnector,
    TxflowConnector,
    VariationalConnector,
    VestConnector,
    WoofiConnector,
)
from src.connectors.real_connectors import (
    AdenRealConnector,
    BinanceRealConnector,
    BingxRealConnector,
    BitgetRealConnector,
    ExtendedRealConnector,
    GateRealConnector,
    HyperliquidRealConnector,
    KucoinRealConnector,
    LighterRealConnector,
    LighterRHRealConnector,
    MexcRealConnector,
    NadoRealConnector,
    OndoRealConnector,
    OkxRealConnector,
    PacificaRealConnector,
    PhemexRealConnector,
    RisexRealConnector,
    TxflowRealConnector,
    VariationalRealConnector,
    VestRealConnector,
)
from src.services.credential_store import CredentialStore
from src.connectors.woofi_connector import WoofiRealConnector
from src.connectors.aster_connector import AsterRealConnector

MOCK_CONNECTOR_MAP: dict[str, type[ExchangeConnector]] = {
    "aden": AdenConnector,
    "aster": AsterConnector,
    "binance": BinanceConnector,
    "bitget": BitgetConnector,
    "bingx": BingxConnector,
    "phemex": PhemexConnector,
    "gate": GateConnector,
    "pacifica": PacificaConnector,
    "mexc": MexcConnector,
    "hyperliquid": HyperliquidConnector,
    "txflow": TxflowConnector,
    "ondo": OndoConnector,
    "risex": RisexConnector,
    "variational": VariationalConnector,
    "extended": ExtendedConnector,
    "okx": OkxConnector,
    "kucoin": KucoinConnector,
    "vest": VestConnector,
    "lighter": LighterConnector,
    "lighter-rh": LighterRHConnector,
    "nado": NadoConnector,
    "woofi": WoofiConnector,
}

REAL_CONNECTOR_MAP: dict[str, type[ExchangeConnector]] = {
    "aden": AdenRealConnector,
    "aster": AsterRealConnector,
    "binance": BinanceRealConnector,
    "bitget": BitgetRealConnector,
    "bingx": BingxRealConnector,
    "phemex": PhemexRealConnector,
    "gate": GateRealConnector,
    "pacifica": PacificaRealConnector,
    "mexc": MexcRealConnector,
    "hyperliquid": HyperliquidRealConnector,
    "txflow": TxflowRealConnector,
    "ondo": OndoRealConnector,
    "risex": RisexRealConnector,
    "variational": VariationalRealConnector,
    "extended": ExtendedRealConnector,
    "okx": OkxRealConnector,
    "kucoin": KucoinRealConnector,
    "vest": VestRealConnector,
    "lighter": LighterRealConnector,
    "lighter-rh": LighterRHRealConnector,
    "nado": NadoRealConnector,
    "woofi": WoofiRealConnector,
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
