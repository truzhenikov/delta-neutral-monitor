from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import BingxRealConnector, RealConnectorNotConfiguredError


class StubBingxConnector(BingxRealConnector):
    def __init__(self) -> None:
        self._responses: list[dict] = []

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert path in {
            "/openApi/swap/v2/user/balance",
            "/openApi/swap/v2/user/positions",
        }
        return self._responses.pop(0)


def test_build_connectors_includes_bingx_real() -> None:
    connectors = build_connectors(["bingx"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "bingx"


def test_bingx_real_connector_requires_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("BINGX_API_KEY", "")
    monkeypatch.setenv("BINGX_API_SECRET", "")

    connector = BingxRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "BINGX_API_KEY/SECRET" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_bingx_real_connector_maps_futures_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("BINGX_API_KEY", "key")
    monkeypatch.setenv("BINGX_API_SECRET", "secret")

    connector = StubBingxConnector()
    connector._responses = [
        {
            "code": 0,
            "msg": "",
            "data": {
                "balance": {
                    "asset": "USDT",
                    "balance": "4514.4501",
                    "equity": "5915.8605",
                    "unrealizedProfit": "1401.4103",
                    "availableMargin": "2394.4592",
                    "usedMargin": "2119.9909",
                    "freezedMargin": "0.0000",
                }
            },
        },
        {
            "code": 0,
            "msg": "",
            "data": [
                {
                    "symbol": "ETH-USDT",
                    "positionAmt": "2.50",
                    "positionSide": "LONG",
                    "avgPrice": "3000",
                    "markPrice": "3100",
                    "leverage": 5,
                    "liquidationPrice": 2500.5,
                    "positionValue": "7750",
                },
                {
                    "symbol": "XAUT-USDT",
                    "positionAmt": "1.2",
                    "positionSide": "SHORT",
                    "avgPrice": "2400",
                    "markPrice": "2380",
                    "leverage": 3,
                    "liquidationPrice": 2600.0,
                    "positionValue": "2856",
                },
            ],
        },
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "bingx"
    assert snapshot.equity_usd == 5915.8605
    assert snapshot.available_margin_usd == 2394.4592
    assert snapshot.maintenance_margin_usd == 2119.9909
    assert len(snapshot.positions) == 2

    first = snapshot.positions[0]
    assert first.symbol == "ETH-USDT"
    assert first.side == "long"
    assert first.size == 2.5
    assert first.entry_price == 3000.0
    assert first.mark_price == 3100.0
    assert first.leverage == 5.0
    assert first.liquidation_price == 2500.5

    second = snapshot.positions[1]
    assert second.symbol == "XAUT-USDT"
    assert second.side == "short"
    assert second.size == 1.2
    assert second.mark_price == 2380.0

    get_settings.cache_clear()
