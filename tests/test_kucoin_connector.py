from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import KucoinRealConnector, RealConnectorNotConfiguredError


class StubKucoinConnector(KucoinRealConnector):
    def __init__(self) -> None:
        self._responses: list[dict] = []

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert path in {
            "/api/v1/account-overview",
            "/api/v1/positions",
            "/api/v1/contracts/active",
        }
        return self._responses.pop(0)


def test_build_connectors_includes_kucoin_mock() -> None:
    connectors = build_connectors(["kucoin"], use_mock_data=True)

    assert len(connectors) == 1
    assert connectors[0].exchange == "kucoin"


def test_kucoin_real_connector_requires_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("KUCOIN_API_KEY", "")
    monkeypatch.setenv("KUCOIN_API_SECRET", "")
    monkeypatch.setenv("KUCOIN_API_PASSPHRASE", "")

    connector = KucoinRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "KUCOIN_API_KEY/SECRET/PASSPHRASE" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_kucoin_real_connector_maps_futures_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("KUCOIN_API_KEY", "key")
    monkeypatch.setenv("KUCOIN_API_SECRET", "secret")
    monkeypatch.setenv("KUCOIN_API_PASSPHRASE", "pass")

    connector = StubKucoinConnector()
    connector._responses = [
        {
            "code": "200000",
            "data": {
                "accountEquity": "1250.5",
                "availableBalance": "900.25",
                "unrealisedPNL": "10.0",
                "marginBalance": "1000.0",
                "positionMargin": "120.0",
                "orderMargin": "30.0",
            },
        },
        {
            "code": "200000",
            "data": [
                {
                    "symbol": "XBTUSDTM",
                    "currentQty": "2",
                    "avgEntryPrice": "61000",
                    "markPrice": "62000",
                    "realLeverage": "5",
                    "liquidationPrice": "55000",
                },
                {
                    "symbol": "XAUTUSDTM",
                    "currentQty": "-3",
                    "avgEntryPrice": "2400",
                    "markPrice": "2380",
                    "realLeverage": "4",
                    "liquidationPrice": "2600",
                },
            ],
        },
        {
            "code": "200000",
            "data": [
                {"symbol": "XBTUSDTM", "multiplier": "0.001"},
                {"symbol": "XAUTUSDTM", "multiplier": "0.01"},
            ],
        },
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "kucoin"
    assert snapshot.equity_usd == 1250.5
    assert snapshot.available_margin_usd == 900.25
    assert snapshot.maintenance_margin_usd == 150.0
    assert len(snapshot.positions) == 2

    btc = snapshot.positions[0]
    assert btc.symbol == "XBTUSDTM"
    assert btc.side == "long"
    assert btc.size == 0.002
    assert btc.entry_price == 61000.0
    assert btc.mark_price == 62000.0
    assert btc.leverage == 5.0
    assert btc.liquidation_price == 55000.0

    gold = snapshot.positions[1]
    assert gold.symbol == "XAUTUSDTM"
    assert gold.side == "short"
    assert gold.size == 0.03
    assert gold.mark_price == 2380.0
    assert gold.leverage == 4.0

    get_settings.cache_clear()
