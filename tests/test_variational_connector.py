from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import RealConnectorNotConfiguredError, VariationalRealConnector


class StubVariationalConnector(VariationalRealConnector):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str] | None, dict | None]] = []

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        self.calls.append((path, headers, params))
        assert headers is not None
        assert headers["Accept"] == "application/json"
        assert "Mozilla/5.0" in headers["User-Agent"]
        assert headers["Cookie"].startswith("vr-token=eyJ")
        assert headers["vr-connected-address"] == "0x5ffa22c26988633e4d8aa4e5fc78a0d1f76f44c3"
        assert headers["Origin"] == "https://omni.variational.io"
        assert headers["Referer"] == "https://omni.variational.io/"

        if path == "/api/positions":
            return [
                {
                    "position_info": {
                        "instrument": {
                            "instrument_type": "perpetual_future",
                            "underlying": "BTC",
                            "settlement_asset": "USDC",
                        },
                        "qty": "0.125",
                        "avg_entry_price": "117250.0",
                    },
                    "price_info": {
                        "price": "118500.25",
                    },
                    "estimated_liquidation_price": "102000.0",
                    "value": "14812.53",
                },
                {
                    "position_info": {
                        "instrument": {
                            "instrument_type": "perpetual_future",
                            "underlying": "ETH",
                            "settlement_asset": "USDC",
                        },
                        "qty": "-3.5",
                        "avg_entry_price": "3550.4",
                    },
                    "price_info": {
                        "price": "3625.8",
                    },
                    "estimated_liquidation_price": "4120.2",
                    "value": "12690.3",
                },
                {
                    "position_info": {
                        "instrument": {
                            "instrument_type": "perpetual_future",
                            "underlying": "SOL",
                            "settlement_asset": "USDC",
                        },
                        "qty": "0",
                        "avg_entry_price": "0",
                    },
                    "price_info": {
                        "price": "0",
                    },
                    "estimated_liquidation_price": "0",
                    "value": "0",
                },
            ]
        if path == "/api/portfolio":
            assert params == {"compute_margin": "true"}
            return {
                "balance": "1281.4982369992",
                "upnl": "18.5017630008",
                "margin_usage": {
                    "initial_margin": "411.6",
                    "maintenance_margin": "123.45",
                },
            }
        raise AssertionError(f"Unexpected path: {path}")


def test_build_connectors_includes_variational_real() -> None:
    connectors = build_connectors(["variational"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "variational"


def test_build_connectors_includes_variational_mock() -> None:
    connectors = build_connectors(["variational"], use_mock_data=True)

    assert len(connectors) == 1
    assert connectors[0].exchange == "variational"


def test_variational_real_connector_requires_vr_token(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("VARIATIONAL_VR_TOKEN", "")

    connector = VariationalRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "VARIATIONAL_VR_TOKEN" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_variational_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv(
        "VARIATIONAL_VR_TOKEN",
        "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzI1NiJ9.eyJhZGRyZXNzIjoiMHg1ZmZhMjJjMjY5ODg2MzNlNGQ4YWE0ZTVmYzc4YTBkMWY3NmY0NGMzIn0.signature",
    )
    monkeypatch.setenv("VARIATIONAL_API_BASE", "https://omni.variational.io")

    connector = StubVariationalConnector()
    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert [path for path, _, _ in connector.calls] == ["/api/portfolio", "/api/positions"]
    assert snapshot.exchange == "variational"
    # Variational portfolio.balance already includes current portfolio value;
    # upnl must not be added again or equity will be understated/overstated.
    assert snapshot.equity_usd == 1281.4982369992
    assert abs(snapshot.available_margin_usd - 869.8982369992) < 1e-9
    assert snapshot.maintenance_margin_usd == 123.45
    assert len(snapshot.positions) == 2

    first = snapshot.positions[0]
    assert first.symbol == "BTC-PERP"
    assert first.side == "long"
    assert first.size == 0.125
    assert first.entry_price == 117250.0
    assert first.mark_price == 118500.25
    assert first.leverage == 11.558759561531296
    assert first.liquidation_price == 102000.0

    second = snapshot.positions[1]
    assert second.symbol == "ETH-PERP"
    assert second.side == "short"
    assert second.size == 3.5
    assert second.entry_price == 3550.4
    assert second.mark_price == 3625.8
    assert second.leverage == 9.90270578109888
    assert second.liquidation_price == 4120.2

    get_settings.cache_clear()
