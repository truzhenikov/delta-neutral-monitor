from __future__ import annotations

import asyncio

import pytest

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import HyperliquidRealConnector, RealConnectorNotConfiguredError


class StubHyperliquidConnector(HyperliquidRealConnector):
    def __init__(self) -> None:
        self._responses: list[object] = []

    async def _post(self, base_url: str, path: str, body: dict, headers=None) -> object:
        assert path == "/info"
        assert headers == {"Content-Type": "application/json"}
        return self._responses.pop(0)


def test_build_connectors_includes_hyperliquid_real() -> None:
    connectors = build_connectors(["hyperliquid"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "hyperliquid"


def test_hyperliquid_real_connector_requires_user_address(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("HYPERLIQUID_USER_ADDRESS", "")

    connector = HyperliquidRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "HYPERLIQUID_USER_ADDRESS" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_hyperliquid_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("HYPERLIQUID_USER_ADDRESS", "0xuser")

    connector = StubHyperliquidConnector()
    connector._responses = [
        {
            "marginSummary": {"accountValue": "7210.5"},
            "withdrawable": "6600.25",
            "assetPositions": [
                {
                    "position": {
                        "coin": "BTC",
                        "szi": "0.12",
                        "entryPx": "101000",
                        "markPx": "104000",
                        "leverage": {"value": "5"},
                        "liquidationPx": "88000",
                    }
                },
                {
                    "position": {
                        "coin": "ETH",
                        "szi": "-3",
                        "entryPx": "2500",
                        "markPx": "2400",
                        "leverage": {"value": "4"},
                        "liquidationPx": "2900",
                    }
                },
            ],
        },
        [
            {"universe": [{"name": "BTC"}, {"name": "ETH"}]},
            [{"markPx": "104500"}, {"markPx": "2395"}],
        ],
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "hyperliquid"
    assert snapshot.equity_usd == 7210.5
    assert snapshot.available_margin_usd == 6600.25
    assert snapshot.maintenance_margin_usd == pytest.approx(215.2125)
    assert len(snapshot.positions) == 2

    btc = snapshot.positions[0]
    assert btc.symbol == "BTC-PERP"
    assert btc.side == "long"
    assert btc.size == 0.12
    assert btc.entry_price == 101000.0
    assert btc.mark_price == 104500.0
    assert btc.leverage == 5.0
    assert btc.liquidation_price == 88000.0

    eth = snapshot.positions[1]
    assert eth.symbol == "ETH-PERP"
    assert eth.side == "short"
    assert eth.size == 3.0
    assert eth.entry_price == 2500.0
    assert eth.mark_price == 2395.0
    assert eth.leverage == 4.0
    assert eth.liquidation_price == 2900.0

    get_settings.cache_clear()
