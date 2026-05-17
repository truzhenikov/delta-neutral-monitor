from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import BitgetRealConnector, RealConnectorNotConfiguredError


class StubBitgetConnector(BitgetRealConnector):
    def __init__(self) -> None:
        self._responses: list[dict] = []

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert path in {
            "/api/v2/mix/account/accounts",
            "/api/v2/mix/position/all-position",
        }
        assert params is not None
        assert headers is not None
        assert headers["ACCESS-KEY"] == "key"
        assert headers["ACCESS-PASSPHRASE"] == "pass"
        assert headers["Content-Type"] == "application/json"
        return self._responses.pop(0)


def test_build_connectors_includes_bitget_real() -> None:
    connectors = build_connectors(["bitget"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "bitget"


def test_bitget_real_connector_requires_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("BITGET_API_KEY", "")
    monkeypatch.setenv("BITGET_API_SECRET", "")
    monkeypatch.setenv("BITGET_API_PASSPHRASE", "")

    connector = BitgetRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "BITGET_API_KEY/SECRET/PASSPHRASE" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_bitget_real_connector_maps_futures_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("BITGET_API_KEY", "key")
    monkeypatch.setenv("BITGET_API_SECRET", "secret")
    monkeypatch.setenv("BITGET_API_PASSPHRASE", "pass")

    connector = StubBitgetConnector()
    connector._responses = [
        {
            "code": "00000",
            "data": [
                {
                    "marginCoin": "USDT",
                    "usdtEquity": "3510.25",
                    "available": "2800.10",
                    "locked": "50",
                    "accountEquity": "3510.25",
                }
            ],
        },
        {
            "code": "00000",
            "data": [
                {
                    "symbol": "BTCUSDT",
                    "total": "0.25",
                    "holdSide": "long",
                    "markPrice": "104000",
                    "openPriceAvg": "101500",
                    "leverage": "5",
                    "liquidationPrice": "89000",
                    "marginSize": "520",
                    "keepMarginRate": "0.02",
                },
                {
                    "symbol": "ETHUSDT",
                    "total": "1.5",
                    "holdSide": "short",
                    "markPrice": "2400",
                    "openPriceAvg": "2500",
                    "leverage": "4",
                    "liquidationPrice": "2850",
                    "marginSize": "300",
                    "keepMarginRate": "0.015",
                },
            ],
        },
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "bitget"
    assert snapshot.equity_usd == 3510.25
    assert snapshot.available_margin_usd == 2800.10
    assert snapshot.maintenance_margin_usd == 14.9
    assert len(snapshot.positions) == 2

    btc = snapshot.positions[0]
    assert btc.symbol == "BTCUSDT"
    assert btc.side == "long"
    assert btc.size == 0.25
    assert btc.entry_price == 101500.0
    assert btc.mark_price == 104000.0
    assert btc.leverage == 5.0
    assert btc.liquidation_price == 89000.0

    eth = snapshot.positions[1]
    assert eth.symbol == "ETHUSDT"
    assert eth.side == "short"
    assert eth.size == 1.5
    assert eth.entry_price == 2500.0
    assert eth.mark_price == 2400.0
    assert eth.leverage == 4.0
    assert eth.liquidation_price == 2850.0

    get_settings.cache_clear()
