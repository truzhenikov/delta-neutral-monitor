from __future__ import annotations

import asyncio
import hashlib
import hmac

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import (
    BinanceRealConnector,
    MexcRealConnector,
    RealConnectorNotConfiguredError,
)


class StubBinanceConnector(BinanceRealConnector):
    def __init__(self) -> None:
        self._responses: dict[str, dict] = {}

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert base_url == "https://fapi.binance.com"
        assert headers is not None
        assert headers["X-MBX-APIKEY"] == "binance-key"
        assert params is not None
        signature = params.pop("signature")
        payload = "&".join(f"{key}={params[key]}" for key in sorted(params))
        expected_signature = hmac.new(b"binance-secret", payload.encode("utf-8"), hashlib.sha256).hexdigest()
        assert signature == expected_signature
        return self._responses[path]


class StubMexcConnector(MexcRealConnector):
    def __init__(self) -> None:
        self._responses: dict[str, dict] = {}

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> dict:
        assert base_url == "https://api.mexc.com"
        assert headers is not None
        assert headers["ApiKey"] == "mexc-key"
        request_time = headers["Request-Time"]
        recv_window = headers.get("Recv-Window", "")
        param_string = "&".join(f"{key}={params[key]}" for key in sorted(params)) if params else ""
        expected_signature = hmac.new(
            b"mexc-secret",
            f"mexc-key{request_time}{param_string}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert headers["Signature"] == expected_signature
        assert recv_window == "5000"
        return self._responses[path]


def test_build_connectors_includes_binance_real_and_mock() -> None:
    real_connectors = build_connectors(["binance"], use_mock_data=False)
    mock_connectors = build_connectors(["binance"], use_mock_data=True)

    assert len(real_connectors) == 1
    assert len(mock_connectors) == 1
    assert real_connectors[0].exchange == "binance"
    assert mock_connectors[0].exchange == "binance"


def test_build_connectors_includes_mexc_real_and_mock() -> None:
    real_connectors = build_connectors(["mexc"], use_mock_data=False)
    mock_connectors = build_connectors(["mexc"], use_mock_data=True)

    assert len(real_connectors) == 1
    assert len(mock_connectors) == 1
    assert real_connectors[0].exchange == "mexc"
    assert mock_connectors[0].exchange == "mexc"


def test_binance_real_connector_requires_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("BINANCE_API_KEY", "")
    monkeypatch.setenv("BINANCE_API_SECRET", "")

    connector = BinanceRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "BINANCE_API_KEY/SECRET" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_mexc_real_connector_requires_credentials(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MEXC_API_KEY", "")
    monkeypatch.setenv("MEXC_API_SECRET", "")

    connector = MexcRealConnector()

    try:
        asyncio.run(connector.fetch_account_snapshot())
    except RealConnectorNotConfiguredError as exc:
        assert "MEXC_API_KEY/SECRET" in str(exc)
    else:
        raise AssertionError("Expected RealConnectorNotConfiguredError")
    finally:
        get_settings.cache_clear()


def test_binance_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("BINANCE_API_KEY", "binance-key")
    monkeypatch.setenv("BINANCE_API_SECRET", "binance-secret")

    connector = StubBinanceConnector()
    connector._responses = {
        "/fapi/v2/account": {
            "assets": [{"asset": "USDT", "walletBalance": "1000.0"}],
            "positions": [
                {
                    "symbol": "BTCUSDT",
                    "positionAmt": "0.25",
                    "entryPrice": "100000",
                    "markPrice": "101000",
                    "leverage": "5",
                    "liquidationPrice": "80000",
                    "positionSide": "LONG",
                    "maintMargin": "125.5",
                },
                {
                    "symbol": "ETHUSDT",
                    "positionAmt": "-2",
                    "entryPrice": "3500",
                    "markPrice": "3400",
                    "leverage": "4",
                    "liquidationPrice": "4200",
                    "positionSide": "SHORT",
                    "maintMargin": "88.0",
                },
            ],
            "totalMarginBalance": "1888.5",
            "availableBalance": "1444.4",
            "totalMaintMargin": "213.5",
        }
    }

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "binance"
    assert snapshot.equity_usd == 1888.5
    assert snapshot.available_margin_usd == 1444.4
    assert snapshot.maintenance_margin_usd == 213.5
    assert len(snapshot.positions) == 2
    assert snapshot.positions[0].side == "long"
    assert snapshot.positions[1].side == "short"

    get_settings.cache_clear()


def test_mexc_real_connector_maps_account_snapshot(monkeypatch) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("MEXC_API_KEY", "mexc-key")
    monkeypatch.setenv("MEXC_API_SECRET", "mexc-secret")

    connector = StubMexcConnector()
    connector._responses = {
        "/api/v1/private/account/assets": {
            "success": True,
            "code": 0,
            "data": [
                {
                    "currency": "USDT",
                    "equity": 2660.91,
                    "availableBalance": 356.41,
                    "positionMargin": 2643.09,
                }
            ],
        },
        "/api/v1/private/position/open_positions": {
            "success": True,
            "code": 0,
            "data": [
                {
                    "symbol": "BTC_USDT",
                    "positionType": 1,
                    "holdVol": 5,
                    "holdAvgPrice": 109777.5,
                    "leverage": 2,
                    "liquidatePrice": 55020.5,
                    "unRealizedPnl": -0.0039,
                },
                {
                    "symbol": "ETH_USDT",
                    "positionType": 2,
                    "holdVol": 3,
                    "holdAvgPrice": 3500,
                    "leverage": 4,
                    "liquidatePrice": 4200,
                    "unRealizedPnl": 12.5,
                },
            ],
        },
    }

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "mexc"
    assert snapshot.equity_usd == 2660.91
    assert snapshot.available_margin_usd == 356.41
    assert snapshot.maintenance_margin_usd == 2643.09
    assert len(snapshot.positions) == 2
    assert snapshot.positions[0].side == "long"
    assert snapshot.positions[1].side == "short"

    get_settings.cache_clear()
