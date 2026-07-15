from __future__ import annotations

import asyncio

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import TxflowRealConnector, RealConnectorNotConfiguredError


class StubTxflowConnector(TxflowRealConnector):
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, dict | None]] = []

    async def _post(self, base_url: str, path: str, body: dict, headers=None) -> object:
        self.calls.append((path, body, headers))
        assert path == "/info"
        assert headers == {"Content-Type": "application/json"}
        return {
            "assetPositions": [
                {
                    "position": {
                        "coin": "LIT-USDC",
                        "szi": "-1500",
                        "entryPx": "2.3253",
                        "markPx": "2.3239",
                        "leverage": {"type": "Cross", "value": 10},
                        "liquidationPx": "2.9381",
                    }
                },
                {
                    "position": {
                        "coin": "BTC-USDC",
                        "szi": "0",
                        "entryPx": "117000",
                        "markPx": "118000",
                        "leverage": {"type": "Cross", "value": 5},
                        "liquidationPx": "100000",
                    }
                },
            ],
            "crossMarginSummary": {
                "accountValue": "989.253451",
                "totalMarginUsed": "348.589270",
            },
            "crossMaintenanceMarginUsed": "52.288390",
            "withdrawable": "640.664180",
        }


def test_build_connectors_includes_txflow_real() -> None:
    connectors = build_connectors(["txflow"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "txflow"


def test_build_connectors_includes_txflow_mock() -> None:
    connectors = build_connectors(["txflow"], use_mock_data=True)

    assert len(connectors) == 1
    assert connectors[0].exchange == "txflow"


def test_txflow_real_connector_requires_user_address(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("TXFLOW_USER_ADDRESS", "")

    connector = TxflowRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "TXFLOW_USER_ADDRESS" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_txflow_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("TXFLOW_USER_ADDRESS", "0xuser")
    monkeypatch.setenv("TXFLOW_API_BASE", "https://api.txflow.com")

    connector = StubTxflowConnector()
    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert connector.calls == [
        (
            "/info",
            {"type": "clearinghouseState", "user": "0xuser"},
            {"Content-Type": "application/json"},
        )
    ]
    assert snapshot.exchange == "txflow"
    assert snapshot.equity_usd == 989.253451
    assert snapshot.available_margin_usd == 640.66418
    assert snapshot.maintenance_margin_usd == 52.28839
    assert len(snapshot.positions) == 1

    lit = snapshot.positions[0]
    assert lit.symbol == "LIT-USDC"
    assert lit.side == "short"
    assert lit.size == 1500.0
    assert lit.entry_price == 2.3253
    assert lit.mark_price == 2.3239
    assert lit.leverage == 10.0
    assert lit.liquidation_price == 2.9381

    get_settings.cache_clear()
