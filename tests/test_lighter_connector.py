from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import LighterRealConnector, RealConnectorNotConfiguredError


class StubLighterConnector(LighterRealConnector):
    def __init__(self) -> None:
        self._responses: dict[tuple[str, tuple[tuple[str, str], ...]], dict] = {}

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert base_url == "https://mainnet.zklighter.elliot.ai"
        assert headers == {"accept": "application/json"}
        normalized_params = tuple(sorted((str(k), str(v)) for k, v in (params or {}).items()))
        key = (path, normalized_params)
        if key not in self._responses:
            raise AssertionError(f"Unexpected request: {key}")
        return self._responses[key]


def test_build_connectors_includes_lighter_real() -> None:
    connectors = build_connectors(["lighter"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "lighter"


def test_build_connectors_includes_lighter_mock() -> None:
    connectors = build_connectors(["lighter"], use_mock_data=True)

    assert len(connectors) == 1
    assert connectors[0].exchange == "lighter"


def test_lighter_real_connector_requires_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LIGHTER_ACCOUNT_INDEX", "")
    monkeypatch.setenv("LIGHTER_L1_ADDRESS", "")

    connector = LighterRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "LIGHTER_ACCOUNT_INDEX or LIGHTER_L1_ADDRESS" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_lighter_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LIGHTER_ACCOUNT_INDEX", "17")
    monkeypatch.setenv("LIGHTER_L1_ADDRESS", "0xabc")

    connector = StubLighterConnector()
    connector._responses[(
        "/api/v1/account",
        (("active_only", "True"), ("by", "index"), ("value", "17")),
    )] = {
        "accounts": [
            {
                "available_balance": "1234.56",
                "collateral": "2000",
                "total_asset_value": "2100.25",
                "cross_maintenance_margin_requirement": "321.09",
                "positions": [
                    {
                        "symbol": "BTC",
                        "sign": 1,
                        "position": "0.5",
                        "avg_entry_price": "100000",
                        "position_value": "55000",
                        "allocated_margin": "11000",
                        "initial_margin_fraction": "20.00",
                        "liquidation_price": "85000",
                    },
                    {
                        "symbol": "ETH",
                        "sign": -1,
                        "position": "1.25",
                        "avg_entry_price": "4000",
                        "position_value": "4750",
                        "allocated_margin": "0",
                        "initial_margin_fraction": "25.00",
                        "liquidation_price": "4800",
                    },
                    {
                        "symbol": "SOL",
                        "sign": 1,
                        "position": "0",
                        "avg_entry_price": "150",
                        "position_value": "0",
                        "allocated_margin": "0",
                        "initial_margin_fraction": "10.00",
                        "liquidation_price": "0",
                    },
                ],
            }
        ]
    }

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "lighter"
    assert snapshot.equity_usd == 2100.25
    assert snapshot.available_margin_usd == 1234.56
    assert snapshot.maintenance_margin_usd == 321.09
    assert len(snapshot.positions) == 2

    btc = snapshot.positions[0]
    assert btc.symbol == "BTC"
    assert btc.side == "long"
    assert btc.size == 0.5
    assert btc.entry_price == 100000.0
    assert btc.mark_price == 110000.0
    assert btc.leverage == 5.0
    assert btc.liquidation_price == 85000.0

    eth = snapshot.positions[1]
    assert eth.symbol == "ETH"
    assert eth.side == "short"
    assert eth.size == 1.25
    assert eth.entry_price == 4000.0
    assert eth.mark_price == 3800.0
    assert eth.leverage == 4.0
    assert eth.liquidation_price == 4800.0

    get_settings.cache_clear()


def test_lighter_real_connector_resolves_account_index_from_wallet(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LIGHTER_ACCOUNT_INDEX", "")
    monkeypatch.setenv("LIGHTER_L1_ADDRESS", "0xdef")

    connector = StubLighterConnector()
    connector._responses[(
        "/api/v1/accountsByL1Address",
        (("l1_address", "0xdef"),),
    )] = {
        "sub_accounts": [{"index": 44}]
    }
    connector._responses[(
        "/api/v1/account",
        (("active_only", "True"), ("by", "index"), ("value", "44")),
    )] = {
        "accounts": [
            {
                "available_balance": "99",
                "collateral": "100",
                "total_asset_value": "101",
                "cross_maintenance_margin_requirement": "5",
                "positions": [],
            }
        ]
    }

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "lighter"
    assert snapshot.equity_usd == 101.0
    assert snapshot.available_margin_usd == 99.0
    assert snapshot.maintenance_margin_usd == 5.0
    assert snapshot.positions == []

    get_settings.cache_clear()


def test_lighter_real_connector_requires_account_index_for_multi_account_wallet(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LIGHTER_ACCOUNT_INDEX", "")
    monkeypatch.setenv("LIGHTER_L1_ADDRESS", "0xmulti")

    connector = StubLighterConnector()
    connector._responses[(
        "/api/v1/accountsByL1Address",
        (("l1_address", "0xmulti"),),
    )] = {
        "sub_accounts": [{"index": 7}, {"index": 9}]
    }

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "lighter account_index is required" in str(exc)
        assert "7, 9" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()
