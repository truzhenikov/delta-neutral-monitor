from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import OndoRealConnector, RealConnectorNotConfiguredError


class StubOndoConnector(OndoRealConnector):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        self.calls.append((path, headers))
        assert headers is not None
        assert headers["ONDO-KEY-ID"] == "ondoKeyId_test"
        assert headers["ONDO-TIMESTAMP"].isdigit()
        assert len(headers["ONDO-SIGN"]) == 64
        assert headers["Accept"] == "application/json"
        assert "Mozilla/5.0" in headers["User-Agent"]

        if path == "/v1/perps/balance":
            return {
                "success": True,
                "result": {
                    "walletBalance": "1998.456854",
                    "realizedPnl": "0",
                    "unrealizedPnl": "9.90649",
                    "marginBalance": "2008.363344",
                    "usedMargin": "1283.381649",
                    "availableMargin": "724.981695",
                    "withdrawableMargin": "724.981695",
                    "maintenanceMarginRequirement": "641.690825",
                    "totalMaintenanceMargin": "641.690825",
                    "marginRatio": "0.3196",
                    "leverage": "6.4",
                    "underLiquidation": False,
                    "totalFundingPayments": "-0.094864",
                    "totalTradingFees": "1.282392",
                    "totalPnL": "8.529234",
                    "netInvested": "1998.551718",
                },
            }
        if path == "/v1/perps/positions":
            return {
                "success": True,
                "result": [
                    {
                        "market": "CRCL-USD.P",
                        "direction": "long",
                        "netQuantity": "100",
                        "averageEntryPrice": "63.692",
                        "usedMargin": "637.87815",
                        "unrealizedPnl": "9.5815",
                        "markPrice": "63.787815",
                        "liquidationPrice": "49.41",
                        "bankruptcyPrice": "43.71",
                        "maintenanceMargin": "318.939075",
                        "notionalValue": "6378.7815",
                        "leverage": "10",
                        "netFundingSinceNeutral": "-0.054201",
                        "returnOnEquity": "0.015043490548263518181247252402185517804",
                    },
                    {
                        "market": "GOOGL-USD.P",
                        "direction": "short",
                        "netQuantity": "18",
                        "averageEntryPrice": "358.595",
                        "usedMargin": "645.503499",
                        "unrealizedPnl": "0.32499",
                        "markPrice": "358.613055",
                        "liquidationPrice": "410.7",
                        "bankruptcyPrice": "447.04",
                        "maintenanceMargin": "322.7517495",
                        "notionalValue": "6455.03499",
                        "leverage": "10",
                        "netFundingSinceNeutral": "-0.040663",
                        "returnOnEquity": "0.0005034927982821846372648809938788884396",
                    },
                    {
                        "market": "TSLA-USD.P",
                        "direction": "neutral",
                        "netQuantity": "0",
                        "averageEntryPrice": "0",
                        "usedMargin": "0",
                        "unrealizedPnl": "0",
                        "markPrice": "0",
                        "liquidationPrice": "0",
                        "bankruptcyPrice": "0",
                        "maintenanceMargin": "0",
                        "notionalValue": "0",
                        "leverage": "0",
                        "netFundingSinceNeutral": "0",
                        "returnOnEquity": "0",
                    },
                ],
            }
        raise AssertionError(f"Unexpected path: {path}")


def test_build_connectors_includes_ondo_real() -> None:
    connectors = build_connectors(["ondo"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "ondo"


def test_build_connectors_includes_ondo_mock() -> None:
    connectors = build_connectors(["ondo"], use_mock_data=True)

    assert len(connectors) == 1
    assert connectors[0].exchange == "ondo"


def test_ondo_real_connector_requires_api_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ONDO_API_KEY", "")
    monkeypatch.setenv("ONDO_API_SECRET", "")

    connector = OndoRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "ONDO_API_KEY" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_ondo_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("ONDO_API_KEY", "ondoKeyId_test")
    monkeypatch.setenv("ONDO_API_SECRET", "ondoApiSecret_test")
    monkeypatch.setenv("ONDO_API_BASE", "https://api.ondoperps.xyz")

    connector = StubOndoConnector()
    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert [path for path, _ in connector.calls] == ["/v1/perps/balance", "/v1/perps/positions"]
    assert snapshot.exchange == "ondo"
    assert snapshot.equity_usd == 2008.363344
    assert snapshot.available_margin_usd == 724.981695
    assert snapshot.maintenance_margin_usd == 641.690825
    assert len(snapshot.positions) == 2

    first = snapshot.positions[0]
    assert first.symbol == "CRCL-USD.P"
    assert first.side == "long"
    assert first.size == 100.0
    assert first.entry_price == 63.692
    assert first.mark_price == 63.787815
    assert first.leverage == 10.0
    assert first.liquidation_price == 49.41

    second = snapshot.positions[1]
    assert second.symbol == "GOOGL-USD.P"
    assert second.side == "short"
    assert second.size == 18.0
    assert second.entry_price == 358.595
    assert second.mark_price == 358.613055
    assert second.leverage == 10.0
    assert second.liquidation_price == 410.7

    get_settings.cache_clear()
