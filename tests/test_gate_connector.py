from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import GateRealConnector, RealConnectorNotConfiguredError


class StubGateConnector(GateRealConnector):
    def __init__(self) -> None:
        self._responses: list[object] = []

    async def _get(self, base_url: str, path: str, params=None, headers=None):
        assert base_url == "https://api.gateio.ws/api/v4"
        assert path in {"/futures/usdt/accounts", "/futures/usdt/positions"}
        assert params is None
        assert headers is not None
        assert headers["KEY"] == "key"
        assert headers["Content-Type"] == "application/json"
        assert headers["Accept"] == "application/json"
        assert headers["Timestamp"]
        assert headers["SIGN"]
        return self._responses.pop(0)


def test_build_connectors_includes_gate_real() -> None:
    connectors = build_connectors(["gate"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "gate"


def test_gate_real_connector_requires_credentials(monkeypatch, tmp_path) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CREDENTIAL_STORE_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("GATE_API_KEY", "")
    monkeypatch.setenv("GATE_API_SECRET", "")

    connector = GateRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "GATE_API_KEY/SECRET" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_gate_real_connector_maps_futures_account_snapshot(monkeypatch, tmp_path) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CREDENTIAL_STORE_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("GATE_API_KEY", "key")
    monkeypatch.setenv("GATE_API_SECRET", "secret")
    monkeypatch.setenv("GATE_API_BASE", "https://api.gateio.ws/api/v4")
    monkeypatch.setenv("GATE_SETTLE_CURRENCY", "usdt")

    connector = StubGateConnector()
    connector._responses = [
        {
            "total": "3510.25",
            "available": "2800.10",
            "maintenance_margin": "14.90",
        },
        [
            {
                "contract": "BTC_USDT",
                "size": "2",
                "entry_price": "101500",
                "mark_price": "104000",
                "leverage": "5",
                "liq_price": "89000",
                "maintenance_margin": "10.40",
            },
            {
                "contract": "ETH_USDT",
                "size": "-3",
                "entry_price": "2500",
                "mark_price": "2400",
                "leverage": "4",
                "liq_price": "2850",
                "maintenance_margin": "4.50",
            },
        ],
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "gate"
    assert snapshot.equity_usd == 3510.25
    assert snapshot.available_margin_usd == 2800.10
    assert snapshot.maintenance_margin_usd == 14.9
    assert len(snapshot.positions) == 2

    btc = snapshot.positions[0]
    assert btc.symbol == "BTC_USDT"
    assert btc.side == "long"
    assert btc.size == 2.0
    assert btc.entry_price == 101500.0
    assert btc.mark_price == 104000.0
    assert btc.leverage == 5.0
    assert btc.liquidation_price == 89000.0

    eth = snapshot.positions[1]
    assert eth.symbol == "ETH_USDT"
    assert eth.side == "short"
    assert eth.size == 3.0
    assert eth.entry_price == 2500.0
    assert eth.mark_price == 2400.0
    assert eth.leverage == 4.0
    assert eth.liquidation_price == 2850.0

    get_settings.cache_clear()


def test_gate_real_connector_normalizes_contract_count_to_base_size(monkeypatch, tmp_path) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CREDENTIAL_STORE_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("GATE_API_KEY", "key")
    monkeypatch.setenv("GATE_API_SECRET", "secret")
    monkeypatch.setenv("GATE_API_BASE", "https://api.gateio.ws/api/v4")
    monkeypatch.setenv("GATE_SETTLE_CURRENCY", "usdt")

    connector = StubGateConnector()
    connector._responses = [
        {
            "total": "1146.355709722932",
            "available": "249.211389722932",
            "maintenance_margin": "191.2092",
        },
        [
            {
                "contract": "SKHYNIX_USDT",
                "size": "-4000",
                "value": "7425.6",
                "entry_price": "1822.23545",
                "mark_price": "1856.4",
                "leverage": "0",
                "liq_price": "2055.8",
                "maintenance_margin": "191.2092",
            }
        ],
    ]

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    pos = snapshot.positions[0]
    assert pos.symbol == "SKHYNIX_USDT"
    assert pos.side == "short"
    assert pos.size == 4.0
    assert pos.notional_usd == 7425.6
    assert round(pos.pnl_usd, 4) == round(-136.6582, 4)

    get_settings.cache_clear()
