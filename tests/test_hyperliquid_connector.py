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
    monkeypatch.setenv("HYPERLIQUID_DEX", "xyz")

    connector = StubHyperliquidConnector()
    connector._responses = [
        {
            "marginSummary": {"accountValue": "7210.5"},
            "crossMaintenanceMarginUsed": "989.6995",
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
        {
            "balances": [],
            "tokenToAvailableAfterMaintenance": [],
        },
        {
            "universe": [],
            "tokens": [],
        },
        {},
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "hyperliquid"
    assert snapshot.equity_usd == 7210.5
    assert snapshot.available_margin_usd == 6600.25
    assert snapshot.maintenance_margin_usd == pytest.approx(989.6995)
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


def test_hyperliquid_real_connector_uses_spot_portfolio_value_when_available(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("HYPERLIQUID_USER_ADDRESS", "0xuser")
    monkeypatch.setenv("HYPERLIQUID_DEX", "xyz,cash")

    connector = StubHyperliquidConnector()
    connector._responses = [
        {
            "marginSummary": {"accountValue": "1000"},
            "crossMaintenanceMarginUsed": "100",
            "withdrawable": "650",
            "assetPositions": [
                {
                    "position": {
                        "coin": "ABC",
                        "szi": "2",
                        "entryPx": "10",
                        "markPx": "11",
                        "leverage": {"value": "3"},
                        "liquidationPx": "5",
                    }
                }
            ],
        },
        [
            {"universe": [{"name": "ABC"}]},
            [{"markPx": "11.5"}],
        ],
        {
            "marginSummary": {"accountValue": "500"},
            "crossMaintenanceMarginUsed": "80",
            "assetPositions": [
                {
                    "position": {
                        "coin": "INTC",
                        "szi": "-1",
                        "entryPx": "50",
                        "markPx": "49",
                        "leverage": {"value": "2"},
                        "liquidationPx": "70",
                    }
                }
            ],
        },
        [
            {"universe": [{"name": "INTC"}]},
            [{"markPx": "48.5"}],
        ],
        {
            "balances": [
                {"coin": "USDC", "token": 0, "total": "1000", "hold": "100", "entryNtl": "0"},
                {"coin": "USDT0", "token": 268, "total": "200", "hold": "50", "entryNtl": "0"},
                {"coin": "KNTQ", "token": 124, "total": "10", "hold": "0", "entryNtl": "0"},
            ],
            "tokenToAvailableAfterMaintenance": [],
        },
        {
            "universe": [
                {"name": "@166", "index": 166, "tokens": [268, 0], "isCanonical": False},
                {"name": "@230", "index": 230, "tokens": [360, 0], "isCanonical": False},
                {"name": "@254", "index": 254, "tokens": [124, 360], "isCanonical": False},
            ],
            "tokens": [
                {"name": "USDC", "index": 0},
                {"name": "USDT0", "index": 268},
                {"name": "USDH", "index": 360},
                {"name": "KNTQ", "index": 124},
            ],
        },
        {
            "@166": "0.99966",
            "@230": "1.00025",
            "@254": "0.168225",
        },
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    expected_spot_portfolio_value = 1000 + (200 * 0.99966) + (10 * 0.168225 * 1.00025)

    assert snapshot.equity_usd == pytest.approx(expected_spot_portfolio_value)
    assert snapshot.maintenance_margin_usd == 180.0
    assert snapshot.available_margin_usd == 1070.0
    assert len(snapshot.positions) == 2
    assert [position.symbol for position in snapshot.positions] == ["ABC-PERP", "INTC-PERP"]

    get_settings.cache_clear()


def test_hyperliquid_real_connector_aggregates_multiple_dexes(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("HYPERLIQUID_USER_ADDRESS", "0xuser")
    monkeypatch.setenv("HYPERLIQUID_DEX", "xyz,cash")

    connector = StubHyperliquidConnector()
    connector._responses = [
        {
            "marginSummary": {"accountValue": "1000"},
            "crossMaintenanceMarginUsed": "100",
            "withdrawable": "650",
            "assetPositions": [
                {
                    "position": {
                        "coin": "ABC",
                        "szi": "2",
                        "entryPx": "10",
                        "markPx": "11",
                        "leverage": {"value": "3"},
                        "liquidationPx": "5",
                    }
                }
            ],
        },
        [
            {"universe": [{"name": "ABC"}]},
            [{"markPx": "11.5"}],
        ],
        {
            "marginSummary": {"accountValue": "500"},
            "crossMaintenanceMarginUsed": "80",
            "assetPositions": [
                {
                    "position": {
                        "coin": "INTC",
                        "szi": "-1",
                        "entryPx": "50",
                        "markPx": "49",
                        "leverage": {"value": "2"},
                        "liquidationPx": "70",
                    }
                }
            ],
        },
        [
            {"universe": [{"name": "INTC"}]},
            [{"markPx": "48.5"}],
        ],
        {
            "balances": [],
            "tokenToAvailableAfterMaintenance": [],
        },
        {
            "universe": [],
            "tokens": [],
        },
        {},
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.equity_usd == 1500.0
    assert snapshot.maintenance_margin_usd == 180.0
    assert snapshot.available_margin_usd == 1070.0
    assert len(snapshot.positions) == 2
    assert [position.symbol for position in snapshot.positions] == ["ABC-PERP", "INTC-PERP"]

    get_settings.cache_clear()


def test_hyperliquid_real_connector_falls_back_when_maintenance_missing(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("HYPERLIQUID_USER_ADDRESS", "0xuser")
    monkeypatch.setenv("HYPERLIQUID_DEX", "cash")

    connector = StubHyperliquidConnector()
    connector._responses = [
        {
            "marginSummary": {"accountValue": "800"},
            "assetPositions": [
                {
                    "position": {
                        "coin": "INTC",
                        "szi": "-2",
                        "entryPx": "50",
                        "markPx": "40",
                        "leverage": {"value": "4"},
                        "liquidationPx": "75",
                    }
                }
            ],
        },
        [
            {"universe": [{"name": "INTC"}]},
            [{"markPx": "40"}],
        ],
        {
            "balances": [],
            "tokenToAvailableAfterMaintenance": [],
        },
        {
            "universe": [],
            "tokens": [],
        },
        {},
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.equity_usd == 800.0
    assert snapshot.maintenance_margin_usd == pytest.approx(1.0)
    assert snapshot.available_margin_usd == pytest.approx(799.0)

    get_settings.cache_clear()
