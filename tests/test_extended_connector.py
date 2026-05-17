from __future__ import annotations

import asyncio

import pytest

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import ExtendedRealConnector, RealConnectorNotConfiguredError


class StubExtendedConnector(ExtendedRealConnector):
    def __init__(self) -> None:
        self._responses: list[dict] = []

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert path in {
            "/api/v1/user/account/info",
            "/api/v1/user/balance",
            "/api/v1/user/positions",
        }
        assert headers == {"X-Api-Key": "key"}
        return self._responses.pop(0)


def test_build_connectors_includes_extended_real() -> None:
    connectors = build_connectors(["extended"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "extended"


def test_extended_real_connector_requires_api_key(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("EXTENDED_API_KEY", "")

    connector = ExtendedRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "EXTENDED_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_extended_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("EXTENDED_API_KEY", "key")

    connector = StubExtendedConnector()
    connector._responses = [
        {
            "status": "OK",
            "data": {
                "accountId": 230109,
                "status": "ACTIVE",
                "l2Key": "0x123",
                "l2Vault": "330109",
                "description": "sub vs gtrad",
            },
        },
        {
            "status": "OK",
            "data": {
                "collateralName": "USD",
                "balance": "168.513778",
                "status": "ACTIVE",
                "equity": "7524.084059",
                "spotEquity": "7194.503396",
                "spotEquityForAvailableForTrade": "7194.503396",
                "availableForTrade": "5008.437240",
                "availableForWithdrawal": "168.513778",
                "unrealisedPnl": "161.066885",
                "initialMargin": "2515.646819",
                "marginRatio": "0.1672",
                "updatedTime": 1779030428936,
                "collateralReservedForSpotOrders": "0",
                "exposure": "25156.468185",
                "leverage": "3.3435",
                "accountHealth": "0.8328",
            },
        },
        {
            "status": "OK",
            "data": [
                {
                    "id": 1,
                    "accountId": 230109,
                    "market": "AAPL_24_5-USD",
                    "side": "LONG",
                    "leverage": "10",
                    "size": "56.00",
                    "value": "16740.839768",
                    "openPrice": "291.58",
                    "markPrice": "298.943567297473975941",
                    "liquidationPrice": "181.16",
                    "margin": "837.041988",
                    "unrealisedPnl": "412.330468",
                    "realisedPnl": "1.2",
                    "adl": "2.5",
                    "maxPositionSize": "100",
                    "createdTime": 1701563440000,
                    "updatedTime": 1701563440000,
                },
                {
                    "id": 2,
                    "accountId": 230109,
                    "market": "META_30_5-USD",
                    "side": "SHORT",
                    "leverage": "5",
                    "size": "10.0",
                    "value": "5520.0",
                    "openPrice": "560.0",
                    "markPrice": "552.0",
                    "liquidationPrice": "620.0",
                    "margin": "1104.0",
                    "unrealisedPnl": "80.0",
                    "realisedPnl": "0",
                    "adl": "1.0",
                    "maxPositionSize": "25",
                    "createdTime": 1701563440000,
                    "updatedTime": 1701563440000,
                },
            ],
        },
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "extended"
    assert snapshot.equity_usd == 7524.084059
    assert snapshot.available_margin_usd == 5008.43724
    assert snapshot.maintenance_margin_usd == pytest.approx(1258.0268546648)
    assert len(snapshot.positions) == 2

    first = snapshot.positions[0]
    assert first.symbol == "AAPL_24_5-USD"
    assert first.side == "long"
    assert first.size == 56.0
    assert first.entry_price == 291.58
    assert first.mark_price == 298.94356729747396
    assert first.leverage == 10.0
    assert first.liquidation_price == 181.16

    second = snapshot.positions[1]
    assert second.symbol == "META_30_5-USD"
    assert second.side == "short"
    assert second.size == 10.0
    assert second.entry_price == 560.0
    assert second.mark_price == 552.0
    assert second.leverage == 5.0
    assert second.liquidation_price == 620.0

    get_settings.cache_clear()
