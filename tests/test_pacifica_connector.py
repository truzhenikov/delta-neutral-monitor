from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import PacificaRealConnector, RealConnectorNotConfiguredError


class StubPacificaConnector(PacificaRealConnector):
    def __init__(self) -> None:
        self._responses: dict[str, dict] = {}

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert base_url == "https://api.pacifica.fi"
        assert headers is not None
        assert headers["PF-API-KEY"] == "pac-key"
        assert headers["Accept"] == "application/json"
        if path in {"/api/v1/account", "/api/v1/positions", "/api/v1/account/settings"}:
            assert params == {"account": "wallet-123"}
        else:
            assert params is None
        return self._responses[path]


def test_build_connectors_includes_pacifica_real() -> None:
    connectors = build_connectors(["pacifica"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "pacifica"


def test_build_connectors_includes_pacifica_mock() -> None:
    connectors = build_connectors(["pacifica"], use_mock_data=True)

    assert len(connectors) == 1
    assert connectors[0].exchange == "pacifica"


def test_pacifica_real_connector_requires_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("PACIFICA_API_KEY", "")
    monkeypatch.setenv("PACIFICA_ACCOUNT", "")

    connector = PacificaRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "PACIFICA_API_KEY/PACIFICA_ACCOUNT" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_pacifica_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("PACIFICA_API_KEY", "pac-key")
    monkeypatch.setenv("PACIFICA_ACCOUNT", "wallet-123")

    connector = StubPacificaConnector()
    connector._responses = {
        "/api/v1/account": {
            "success": True,
            "data": {
                "account_equity": "2150.25",
                "available_to_spend": "1800.75",
                "cross_mmr": "420.69",
            },
            "error": None,
            "code": None,
        },
        "/api/v1/positions": {
            "success": True,
            "data": [
                {
                    "symbol": "AAVE",
                    "side": "ask",
                    "amount": "223.72",
                    "entry_price": "279.283134",
                    "margin": "0",
                    "funding": "13.159593",
                    "isolated": False,
                    "liquidation_price": None,
                },
                {
                    "symbol": "BTC",
                    "side": "bid",
                    "amount": "0.15",
                    "entry_price": "102500",
                    "margin": "500",
                    "funding": "1.5",
                    "isolated": True,
                    "liquidation_price": "91000",
                },
                {
                    "symbol": "SOL",
                    "side": "bid",
                    "amount": "0",
                    "entry_price": "150",
                    "liquidation_price": None,
                },
            ],
            "error": None,
            "code": None,
        },
        "/api/v1/account/settings": {
            "success": True,
            "data": {
                "margin_settings": [
                    {
                        "symbol": "AAVE",
                        "isolated": False,
                        "leverage": 5,
                    }
                ]
            },
            "error": None,
            "code": None,
        },
        "/api/v1/info/prices": {
            "success": True,
            "data": [
                {"symbol": "AAVE", "mark": "280.5"},
                {"symbol": "BTC", "mark": "104000"},
            ],
            "error": None,
            "code": None,
        },
        "/api/v1/info": {
            "success": True,
            "data": [
                {"symbol": "AAVE", "max_leverage": 20},
                {"symbol": "BTC", "max_leverage": 50},
            ],
            "error": None,
            "code": None,
        },
    }

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "pacifica"
    assert snapshot.equity_usd == 2150.25
    assert snapshot.available_margin_usd == 1800.75
    assert snapshot.maintenance_margin_usd == 420.69
    assert len(snapshot.positions) == 2

    aave = snapshot.positions[0]
    assert aave.symbol == "AAVE"
    assert aave.side == "short"
    assert aave.size == 223.72
    assert aave.entry_price == 279.283134
    assert aave.mark_price == 280.5
    assert aave.leverage == 5.0
    assert aave.liquidation_price is None

    btc = snapshot.positions[1]
    assert btc.symbol == "BTC"
    assert btc.side == "long"
    assert btc.size == 0.15
    assert btc.entry_price == 102500.0
    assert btc.mark_price == 104000.0
    assert btc.leverage == 50.0
    assert btc.liquidation_price == 91000.0

    get_settings.cache_clear()
