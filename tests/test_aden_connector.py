from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import AdenRealConnector, RealConnectorNotConfiguredError


class StubAdenConnector(AdenRealConnector):
    def __init__(self) -> None:
        self._responses: list[object] = []

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert path in {
            "/api/v1/dex_futures/usdt/accounts",
            "/api/v1/dex_futures/usdt/positions",
        }
        assert params is None
        assert headers is not None
        assert headers["KEY"] == "key"
        assert headers["Accept"] == "application/json"
        assert headers["Content-Type"] == "application/json"
        assert headers["Timestamp"].isdigit()
        assert len(headers["SIGN"]) == 128
        return self._responses.pop(0)


def test_build_connectors_includes_aden_real() -> None:
    connectors = build_connectors(["aden"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "aden"


def test_build_connectors_includes_aden_mock() -> None:
    connectors = build_connectors(["aden"], use_mock_data=True)

    assert len(connectors) == 1
    assert connectors[0].exchange == "aden"


def test_aden_real_connector_requires_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ADEN_API_KEY", "")
    monkeypatch.setenv("ADEN_API_SECRET", "")

    connector = AdenRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "ADEN_API_KEY/SECRET" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_aden_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ADEN_API_KEY", "key")
    monkeypatch.setenv("ADEN_API_SECRET", "secret")

    connector = StubAdenConnector()
    connector._responses = [
        {
            "user": 123,
            "currency": "USDT",
            "total": "5025.4",
            "available": "3210.75",
            "maintenance_margin": "412.5",
            "unrealised_pnl": "120.2",
        },
        [
            {
                "contract": "BTC_USDT",
                "size": "0.15",
                "entry_price": "101500",
                "mark_price": "104000",
                "leverage": "5",
                "liq_price": "89000",
            },
            {
                "contract": "ETH_USDT",
                "size": "-2.5",
                "entry_price": "2500",
                "mark_price": "2400",
                "leverage": "4",
                "liq_price": "2850",
            },
            {
                "contract": "XRP_USDT",
                "size": "0",
                "entry_price": "2",
                "mark_price": "2.1",
                "leverage": "3",
                "liq_price": "0",
            },
        ],
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "aden"
    assert snapshot.equity_usd == 5025.4
    assert snapshot.available_margin_usd == 3210.75
    assert snapshot.maintenance_margin_usd == 412.5
    assert len(snapshot.positions) == 2

    btc = snapshot.positions[0]
    assert btc.symbol == "BTC_USDT"
    assert btc.side == "long"
    assert btc.size == 0.15
    assert btc.entry_price == 101500.0
    assert btc.mark_price == 104000.0
    assert btc.leverage == 5.0
    assert btc.liquidation_price == 89000.0

    eth = snapshot.positions[1]
    assert eth.symbol == "ETH_USDT"
    assert eth.side == "short"
    assert eth.size == 2.5
    assert eth.entry_price == 2500.0
    assert eth.mark_price == 2400.0
    assert eth.leverage == 4.0
    assert eth.liquidation_price == 2850.0

    get_settings.cache_clear()


def test_aden_real_connector_normalizes_contract_size_using_value_field(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ADEN_API_KEY", "key")
    monkeypatch.setenv("ADEN_API_SECRET", "secret")

    connector = StubAdenConnector()
    connector._responses = [
        {
            "total": "2918.109677131344",
            "cross_margin_balance": "2816.772377131344",
            "available": "2495.321527131344",
            "maintenance_margin": "41.14885",
        },
        [
            {
                "contract": "COINX_USDT",
                "size": 2000,
                "value": "3827.8",
                "entry_price": "193.246865",
                "mark_price": "191.39",
                "leverage": "0",
                "lever": "10",
                "liq_price": "47.86",
            }
        ],
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.equity_usd == 2816.772377131344
    assert len(snapshot.positions) == 1
    position = snapshot.positions[0]
    assert position.symbol == "COINX_USDT"
    assert position.side == "long"
    assert round(position.size, 8) == 20.0
    assert position.notional_usd == 3827.8
    assert round(position.pnl_usd, 4) == -37.1373
    assert position.leverage == 10.0

    get_settings.cache_clear()
