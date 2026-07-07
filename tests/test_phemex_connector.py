from __future__ import annotations

import asyncio
import hashlib
import hmac

import pytest

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import PhemexRealConnector, RealConnectorNotConfiguredError


class StubPhemexConnector(PhemexRealConnector):
    def __init__(self) -> None:
        self._response: dict = {}

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert base_url == "https://api.phemex.com"
        assert path == "/g-accounts/accountPositions"
        assert params == {"currency": "USDT"}
        assert headers is not None
        assert headers["x-phemex-access-token"] == "phemex-key"
        expiry = headers["x-phemex-request-expiry"]
        expected_signature = hmac.new(
            b"phemex-secret",
            f"/g-accounts/accountPositionscurrency=USDT{expiry}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert headers["x-phemex-request-signature"] == expected_signature
        return self._response


def test_build_connectors_includes_phemex_real() -> None:
    connectors = build_connectors(["phemex"], use_mock_data=False)

    assert len(connectors) == 1
    assert connectors[0].exchange == "phemex"


def test_build_connectors_includes_phemex_mock() -> None:
    connectors = build_connectors(["phemex"], use_mock_data=True)

    assert len(connectors) == 1
    assert connectors[0].exchange == "phemex"


def test_phemex_real_connector_requires_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("PHEMEX_API_KEY", "")
    monkeypatch.setenv("PHEMEX_API_SECRET", "")

    connector = PhemexRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "PHEMEX_API_KEY/SECRET" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_phemex_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("PHEMEX_API_KEY", "phemex-key")
    monkeypatch.setenv("PHEMEX_API_SECRET", "phemex-secret")
    monkeypatch.setenv("PHEMEX_MARGIN_CURRENCY", "USDT")

    connector = StubPhemexConnector()
    connector._response = {
        "code": 0,
        "msg": "",
        "data": {
            "account": {
                "accountBalanceRv": "2150.25",
                "totalBalanceRv": "2150.25",
                "availableBalanceRv": "1800.75",
                "totalMaintMarginReqRv": "420.69",
            },
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "side": "Buy",
                    "size": "0.15",
                    "avgEntryPriceRp": "102500",
                    "markPriceRp": "104000",
                    "leverageRr": "8",
                    "liquidationPriceRp": "91000",
                },
                {
                    "symbol": "ETHUSDT",
                    "posSide": "Short",
                    "size": "2.5",
                    "avgEntryPriceRp": "3200",
                    "markPriceRp": "3150",
                    "leverageRr": "5",
                    "liquidationPriceRp": "3900",
                },
                {
                    "symbol": "SOLUSDT",
                    "side": "Sell",
                    "size": "0",
                    "avgEntryPriceRp": "150",
                    "markPriceRp": "145",
                },
            ],
        },
    }

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "phemex"
    assert snapshot.equity_usd == 2150.25
    assert snapshot.available_margin_usd == 1800.75
    assert snapshot.maintenance_margin_usd == 420.69
    assert len(snapshot.positions) == 2

    btc = snapshot.positions[0]
    assert btc.symbol == "BTCUSDT"
    assert btc.side == "long"
    assert btc.size == 0.15
    assert btc.entry_price == 102500.0
    assert btc.mark_price == 104000.0
    assert btc.leverage == 8.0
    assert btc.liquidation_price == 91000.0

    eth = snapshot.positions[1]
    assert eth.symbol == "ETHUSDT"
    assert eth.side == "short"
    assert eth.size == 2.5
    assert eth.entry_price == 3200.0
    assert eth.mark_price == 3150.0
    assert eth.leverage == 5.0
    assert eth.liquidation_price == 3900.0

    get_settings.cache_clear()


def test_phemex_real_connector_uses_balance_plus_unrealized_pnl_for_equity(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("PHEMEX_API_KEY", "phemex-key")
    monkeypatch.setenv("PHEMEX_API_SECRET", "phemex-secret")
    monkeypatch.setenv("PHEMEX_MARGIN_CURRENCY", "USDT")

    connector = StubPhemexConnector()
    connector._response = {
        "code": 0,
        "msg": "",
        "data": {
            "account": {
                "accountBalanceRv": "3006.73377022",
                "availableBalanceRv": "356.4193",
                "totalMaintMarginReqRv": "2643.09899022",
            },
            "positions": [
                {
                    "symbol": "METAUSDT",
                    "side": "Buy",
                    "size": "10",
                    "avgEntryPriceRp": "591.02364",
                    "markPriceRp": "603.70",
                    "leverageRr": "1",
                    "liquidationPriceRp": "507.69",
                },
                {
                    "symbol": "DRAMUSDT",
                    "side": "Buy",
                    "size": "120",
                    "avgEntryPriceRp": "65.068190833",
                    "markPriceRp": "61.13",
                    "leverageRr": "1",
                    "liquidationPriceRp": "52.31",
                },
            ],
        },
    }

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.equity_usd == pytest.approx(2660.91447026)
    assert snapshot.available_margin_usd == 356.4193
    assert snapshot.maintenance_margin_usd == 2643.09899022

    get_settings.cache_clear()
