from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import RealConnectorNotConfiguredError, RisexRealConnector


class StubRisexConnector(RisexRealConnector):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str] | None, dict | None]] = []

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        self.calls.append((path, headers, params))
        assert headers is not None
        assert headers["Accept"] == "application/json"
        assert "Mozilla/5.0" in headers["User-Agent"]

        if path == "/v1/markets":
            return {
                "data": {
                    "markets": [
                        {"market_id": "1", "display_name": "BTC/USDC"},
                        {"market_id": "2", "display_name": "ETH/USDC"},
                    ]
                }
            }
        if path == "/v1/portfolio/details":
            assert params == {"account": "0xabc123"}
            return {
                "data": {
                    "account": "0xabc123",
                    "summary": {
                        "free_collateral": "724.981695",
                        "total_account_value": "2008.363344",
                        "total_maintenance_margin": "641.690825",
                    },
                    "positions": [
                        {
                            "market_id": "1",
                            "market_name": "BTC/USDC",
                            "side": 0,
                            "size": "0.125",
                            "avg_entry_price": "63250.5",
                            "mark_price": "64655.72",
                            "leverage": "10",
                            "liquidation_price": "51200.1",
                        },
                        {
                            "market_id": "2",
                            "market_name": "",
                            "side": 1,
                            "size": "-3.5",
                            "avg_entry_price": "1822.4",
                            "mark_price": "1879.89",
                            "leverage": "5",
                            "liquidation_price": "2144.8",
                        },
                        {
                            "market_id": "2",
                            "market_name": "ETH/USDC",
                            "side": 0,
                            "size": "0",
                            "avg_entry_price": "0",
                            "mark_price": "0",
                            "leverage": "0",
                            "liquidation_price": "0",
                        },
                    ],
                }
            }
        raise AssertionError(f"Unexpected path: {path}")


def test_build_connectors_includes_risex_real() -> None:
    connectors = build_connectors(["risex"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "risex"


def test_build_connectors_includes_risex_mock() -> None:
    connectors = build_connectors(["risex"], use_mock_data=True)

    assert len(connectors) == 1
    assert connectors[0].exchange == "risex"


def test_risex_real_connector_requires_account(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("RISEX_ACCOUNT", "")

    connector = RisexRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "RISEX_ACCOUNT" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_risex_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("RISEX_ACCOUNT", "0xabc123")
    monkeypatch.setenv("RISEX_API_BASE", "https://api.rise.trade")

    connector = StubRisexConnector()
    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert [path for path, _, _ in connector.calls] == ["/v1/markets", "/v1/portfolio/details"]
    assert snapshot.exchange == "risex"
    assert snapshot.equity_usd == 2008.363344
    assert snapshot.available_margin_usd == 724.981695
    assert snapshot.maintenance_margin_usd == 641.690825
    assert len(snapshot.positions) == 2

    first = snapshot.positions[0]
    assert first.symbol == "BTC/USDC"
    assert first.side == "long"
    assert first.size == 0.125
    assert first.entry_price == 63250.5
    assert first.mark_price == 64655.72
    assert first.leverage == 10.0
    assert first.liquidation_price == 51200.1

    second = snapshot.positions[1]
    assert second.symbol == "ETH/USDC"
    assert second.side == "short"
    assert second.size == 3.5
    assert second.entry_price == 1822.4
    assert second.mark_price == 1879.89
    assert second.leverage == 5.0
    assert second.liquidation_price == 2144.8

    get_settings.cache_clear()
