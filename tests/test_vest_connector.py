from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import RealConnectorNotConfiguredError, VestRealConnector


class StubVestConnector(VestRealConnector):
    def __init__(self) -> None:
        self._response: dict | None = None

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert base_url == "https://server-prod.hz.vestmarkets.com/v2"
        assert path == "/account"
        assert params is not None
        assert isinstance(params.get("time"), int)
        assert headers is not None
        assert headers["X-API-KEY"] == "vest-key"
        assert headers["xrestservermm"] == "restserver7"
        assert headers["Accept"] == "application/json"
        assert self._response is not None
        return self._response


def test_build_connectors_includes_vest_real() -> None:
    connectors = build_connectors(["vest"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "vest"


def test_build_connectors_includes_vest_mock() -> None:
    connectors = build_connectors(["vest"], use_mock_data=True)

    assert len(connectors) == 1
    assert connectors[0].exchange == "vest"


def test_vest_real_connector_requires_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("VEST_API_KEY", "")
    monkeypatch.setenv("VEST_ACCOUNT_GROUP", "")

    connector = VestRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "VEST_API_KEY/VEST_ACCOUNT_GROUP" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_vest_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("VEST_API_KEY", "vest-key")
    monkeypatch.setenv("VEST_ACCOUNT_GROUP", "7")

    connector = StubVestConnector()
    connector._response = {
        "totalAccountValue": "200000.12",
        "withdrawable": "111222.12",
        "totalMaintMargin": "123232.12",
        "positions": [
            {
                "symbol": "BTC-PERP",
                "isLong": True,
                "size": "0.1",
                "entryPrice": "30000",
                "markPrice": "34000",
                "liqPrice": "20000",
                "initMarginRatio": "0.2",
            },
            {
                "symbol": "ETH-PERP",
                "isLong": False,
                "size": "2.5",
                "entryPrice": "2500",
                "markPrice": "2400",
                "liqPrice": "2850",
            },
            {
                "symbol": "SOL-PERP",
                "isLong": True,
                "size": "0",
                "entryPrice": "150",
                "markPrice": "160",
                "liqPrice": "90",
            },
        ],
        "leverages": [
            {"symbol": "BTC-PERP", "value": 20},
            {"symbol": "ETH-PERP", "value": 4},
        ],
    }

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "vest"
    assert snapshot.equity_usd == 200000.12
    assert snapshot.available_margin_usd == 111222.12
    assert snapshot.maintenance_margin_usd == 123232.12
    assert len(snapshot.positions) == 2

    btc = snapshot.positions[0]
    assert btc.symbol == "BTC-PERP"
    assert btc.side == "long"
    assert btc.size == 0.1
    assert btc.entry_price == 30000.0
    assert btc.mark_price == 34000.0
    assert btc.leverage == 20.0
    assert btc.liquidation_price == 20000.0

    eth = snapshot.positions[1]
    assert eth.symbol == "ETH-PERP"
    assert eth.side == "short"
    assert eth.size == 2.5
    assert eth.entry_price == 2500.0
    assert eth.mark_price == 2400.0
    assert eth.leverage == 4.0
    assert eth.liquidation_price == 2850.0

    get_settings.cache_clear()


def test_vest_real_connector_falls_back_to_margin_ratio_for_leverage(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("VEST_API_KEY", "vest-key")
    monkeypatch.setenv("VEST_ACCOUNT_GROUP", "7")

    connector = StubVestConnector()
    connector._response = {
        "totalAccountValue": "1000",
        "withdrawable": "700",
        "totalMaintMargin": "50",
        "positions": [
            {
                "symbol": "AAPL-USD-PERP",
                "isLong": True,
                "size": "5",
                "entryPrice": "200",
                "markPrice": "210",
                "liqPrice": "100",
                "initMarginRatio": "0.25",
            }
        ],
        "leverages": [],
    }

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert len(snapshot.positions) == 1
    assert snapshot.positions[0].leverage == 4.0

    get_settings.cache_clear()
