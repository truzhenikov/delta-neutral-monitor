"""Sanitized portfolio-margin regression; no live account or credentials."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.connectors import real_connectors as rc


@pytest.fixture
def spot_state():
    return {
        "portfolioMarginEnabled": True,
        "balances": [{"coin": "USDC", "token": 0, "total": "4756.26203398",
                      "hold": "3753.791463", "spotHold": "3753.791463",
                      "supplied": "1002.47057098"}],
        "tokenToAvailableAfterMaintenance": [[0, "1002.47057098"], [150, "0.0"],
                                              [197, "0.0"], [268, "0.0"], [360, "0.0"]],
        "portfolioMarginRatio": "0.0",
        "tokenToPortfolioBorrowRatio": [[0, "0.0"]],
    }


def snapshot(monkeypatch, spot, *, exchange="hyperliquid:fixture", withdrawable="0"):
    monkeypatch.setattr(rc, "get_settings", lambda: SimpleNamespace(
        hyperliquid_user_address="", hyperliquid_dex="xyz,io", hyperliquid_api_base="https://example.invalid"))
    monkeypatch.setattr(rc, "_runtime_credentials", lambda ref: {
        "user_address": "0x" + "12" * 20, "dex": "xyz,io"})
    connector = rc.HyperliquidRealConnector()
    connector.exchange = exchange
    calls = []

    async def post(*, base_url, path, body, headers):
        calls.append(deepcopy(body))
        kind = body["type"]
        if kind == "clearinghouseState":
            return {"marginSummary": {"accountValue": "3754.011463" if body.get("dex") == "xyz" else "0"},
                    "crossMarginSummary": {"accountValue": "0"},
                    "crossMaintenanceMarginUsed": "0", "withdrawable": withdrawable,
                    "assetPositions": [{"position": {
                        "coin": "xyz:TEST", "szi": "1", "entryPx": "100", "markPx": "101",
                        "liquidationPx": "50", "leverage": {"type": "isolated", "value": 3}}}]
                    if body.get("dex") == "xyz" else []}
        if kind == "metaAndAssetCtxs":
            return [{"universe": []}, []]
        if kind == "spotClearinghouseState":
            return deepcopy(spot)
        if kind == "spotMeta":
            return {"tokens": [{"name": "USDC", "index": 0}], "universe": []}
        if kind == "allMids":
            return {}
        raise AssertionError("Unexpected query " + kind)

    connector._post = post
    result = asyncio.run(connector.fetch_account_snapshot())
    assert all(call["type"] in {"clearinghouseState", "metaAndAssetCtxs", "spotClearinghouseState", "spotMeta", "allMids"} for call in calls)
    return result


def test_portfolio_free_balance_uses_native_available(monkeypatch, spot_state):
    result = snapshot(monkeypatch, spot_state)
    assert result.available_margin_usd == pytest.approx(1002.47057098)
    assert result.equity_usd == pytest.approx(4756.26203398)
    assert result.maintenance_margin_usd == 0
    assert result.exchange == "hyperliquid:fixture"
    assert len(result.positions) == 1


def test_portfolio_does_not_add_per_dex_withdrawable(monkeypatch, spot_state):
    assert snapshot(monkeypatch, spot_state, withdrawable="500").available_margin_usd == pytest.approx(1002.47057098)


def test_native_available_not_total_minus_hold_or_supplied(monkeypatch, spot_state):
    spot_state["tokenToAvailableAfterMaintenance"][0][1] = "25.125"
    assert snapshot(monkeypatch, spot_state).available_margin_usd == 25.125


def test_non_usdc_collateral_is_not_counted_as_dollars(monkeypatch, spot_state):
    spot_state["tokenToAvailableAfterMaintenance"][1][1] = "2000"
    assert snapshot(monkeypatch, spot_state).available_margin_usd == pytest.approx(1002.47057098)


@pytest.mark.parametrize("rows", [None, [], [[150, "12"]]])
def test_missing_usdc_available_is_unknown_not_known_zero(monkeypatch, spot_state, rows):
    if rows is None:
        spot_state.pop("tokenToAvailableAfterMaintenance")
    else:
        spot_state["tokenToAvailableAfterMaintenance"] = rows
    with pytest.raises(rc.RealConnectorRequestError, match="portfolio.*available"):
        snapshot(monkeypatch, spot_state)


@pytest.mark.parametrize("rows", [
    [[0, "NaN"]], [[0, "Infinity"]], [[0, "-Infinity"]], [[0, "bad"]],
    [[0, None]], [[0, True]], [[0, "1e9999"]], [[0, "1e9999999"]], [[0, "1"], [0, "2"]],
    [[0, "1"], [150, "1"], [150, "2"]], [["0", "1"]], [[False, "1"]],
    [[0.0, "1"]], [[-1, "1"]], [[0]], {"0": "1"}, "invalid",
    [[0, "1"], [150, "NaN"]],
])
def test_malformed_available_fails_closed(monkeypatch, spot_state, rows):
    spot_state["tokenToAvailableAfterMaintenance"] = rows
    with pytest.raises(rc.RealConnectorRequestError, match="portfolio.*available"):
        snapshot(monkeypatch, spot_state)


@pytest.mark.parametrize("value, expected", [("0", 0), ("-12.5", 0), ("0.125", 0.125)])
def test_finite_available_is_clamped_at_zero(monkeypatch, spot_state, value, expected):
    spot_state["tokenToAvailableAfterMaintenance"][0][1] = value
    assert snapshot(monkeypatch, spot_state).available_margin_usd == expected


@pytest.mark.parametrize("mode", [False, None])
def test_ordinary_account_retains_existing_per_dex_mapping(monkeypatch, spot_state, mode):
    if mode is None:
        spot_state.pop("portfolioMarginEnabled")
    else:
        spot_state["portfolioMarginEnabled"] = mode
    spot_state["tokenToAvailableAfterMaintenance"] = [[0, "99999"]]
    assert snapshot(monkeypatch, spot_state, withdrawable="12").available_margin_usd == 24
