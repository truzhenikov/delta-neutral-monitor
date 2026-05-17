from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import OkxRealConnector, RealConnectorNotConfiguredError


class StubOkxConnector(OkxRealConnector):
    def __init__(self) -> None:
        self._responses: list[dict] = []

    def _okx_timestamp(self) -> str:
        return "2026-05-17T12:34:56.789Z"

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert path in {
            "/api/v5/account/balance",
            "/api/v5/account/positions",
        }
        assert headers is not None
        assert headers["OK-ACCESS-KEY"] == "key"
        assert headers["OK-ACCESS-PASSPHRASE"] == "pass"
        assert headers["OK-ACCESS-TIMESTAMP"] == "2026-05-17T12:34:56.789Z"
        assert headers["Content-Type"] == "application/json"
        return self._responses.pop(0)


def test_build_connectors_includes_okx_real() -> None:
    connectors = build_connectors(["okx"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "okx"


def test_okx_real_connector_requires_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("OKX_API_KEY", "")
    monkeypatch.setenv("OKX_API_SECRET", "")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "")

    connector = OkxRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "OKX_API_KEY/SECRET/PASSPHRASE" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_okx_real_connector_maps_futures_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("OKX_API_KEY", "key")
    monkeypatch.setenv("OKX_API_SECRET", "secret")
    monkeypatch.setenv("OKX_API_PASSPHRASE", "pass")

    connector = StubOkxConnector()
    connector._responses = [
        {
            "code": "0",
            "data": [
                {
                    "totalEq": "5025.5",
                    "availEq": "4100.25",
                    "mmr": "210.75",
                    "details": [],
                }
            ],
        },
        {
            "code": "0",
            "data": [
                {
                    "instId": "BTC-USDT-SWAP",
                    "pos": "0.5",
                    "posSide": "long",
                    "avgPx": "103000",
                    "markPx": "104500",
                    "lever": "5",
                    "liqPx": "89000",
                },
                {
                    "instId": "ETH-USDT-SWAP",
                    "pos": "-2",
                    "posSide": "net",
                    "avgPx": "2450",
                    "markPx": "2400",
                    "lever": "4",
                    "liqPx": "2900",
                },
            ],
        },
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "okx"
    assert snapshot.equity_usd == 5025.5
    assert snapshot.available_margin_usd == 4100.25
    assert snapshot.maintenance_margin_usd == 210.75
    assert len(snapshot.positions) == 2

    btc = snapshot.positions[0]
    assert btc.symbol == "BTC-USDT-SWAP"
    assert btc.side == "long"
    assert btc.size == 0.5
    assert btc.entry_price == 103000.0
    assert btc.mark_price == 104500.0
    assert btc.leverage == 5.0
    assert btc.liquidation_price == 89000.0

    eth = snapshot.positions[1]
    assert eth.symbol == "ETH-USDT-SWAP"
    assert eth.side == "short"
    assert eth.size == 2.0
    assert eth.entry_price == 2450.0
    assert eth.mark_price == 2400.0
    assert eth.leverage == 4.0
    assert eth.liquidation_price == 2900.0

    get_settings.cache_clear()
