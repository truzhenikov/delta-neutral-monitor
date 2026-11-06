"""Account-wide HIP-3 discovery, with synthetic public identities only."""
import asyncio
from copy import deepcopy
from types import SimpleNamespace

import pytest

from src.connectors import real_connectors as rc
from src.core.models import Position
from src.services.monitoring import MonitoringService


EMPTY = {"marginSummary": {"accountValue": "0"}, "assetPositions": [],
         "withdrawable": "0", "crossMaintenanceMarginUsed": "0"}


def setup_connector(monkeypatch, *, configured="xyz", registry=None, failure=None):
    monkeypatch.setattr(rc, "get_settings", lambda: SimpleNamespace(
        hyperliquid_user_address="0xfixture", hyperliquid_dex="env-only",
        hyperliquid_api_base="https://example.invalid"))
    monkeypatch.setattr(rc, "_runtime_credentials", lambda ref: {
        "user_address": "0xfixture", "dex": configured})
    connector = rc.HyperliquidRealConnector()
    connector.exchange = "hyperliquid:fixture"
    calls = []
    states = {"": deepcopy(EMPTY), "xyz": deepcopy(EMPTY), "para": deepcopy(EMPTY)}
    for dex, size, equity, available, maintenance in [
        ("xyz", "41", "1000", "100", "10"), ("para", "10", "200", "20", "2")
    ]:
        states[dex].update(marginSummary={"accountValue": equity}, withdrawable=available,
                           crossMaintenanceMarginUsed=maintenance,
                           assetPositions=[{"position": {"coin": f"{dex}:UNITREE", "szi": size,
                               "entryPx": "100", "leverage": {"value": "3"}}}])
    payload = [None, {"name": "xyz"}, {"name": "para"}] if registry is None else registry

    async def post(*, base_url, path, body, headers):
        assert path == "/info"
        assert base_url == "https://example.invalid"
        calls.append(deepcopy(body))
        kind, dex = body["type"], body.get("dex", "")
        if failure and (kind, dex) == failure:
            raise rc.RealConnectorRequestError("synthetic DEX request failure")
        if kind == "perpDexs":
            return deepcopy(payload)
        if kind == "clearinghouseState":
            return deepcopy(states.get(dex, EMPTY))
        if kind == "metaAndAssetCtxs":
            return [{"universe": [{"name": f"{dex}:UNITREE"}]}, [{"markPx": "101"}]]
        if kind == "spotClearinghouseState":
            return {"balances": []}
        if kind == "spotMeta":
            return {"universe": [], "tokens": []}
        if kind == "allMids":
            return {}
        raise AssertionError(f"Unexpected request: {kind}")

    connector._post = post
    return connector, calls, states


def test_discovers_para_and_preserves_distinct_symbols_and_hedge(monkeypatch):
    connector, calls, states = setup_connector(monkeypatch)
    result = asyncio.run(connector.fetch_account_snapshot())
    assert [(p.symbol, p.size) for p in result.positions] == [
        ("xyz:UNITREE-PERP", 41), ("para:UNITREE-PERP", 10)]
    assert result.equity_usd == 1200
    assert result.available_margin_usd == 120
    assert result.maintenance_margin_usd == 12
    assert result.exchange == "hyperliquid:fixture"
    assert [c for c in calls if c["type"] == "perpDexs"] == [{"type": "perpDexs"}]
    assert {c.get("dex", "") for c in calls if c["type"] == "clearinghouseState"} == {"", "xyz", "para"}
    assert {c.get("dex", "") for c in calls if c["type"] == "metaAndAssetCtxs"} == {"xyz", "para"}
    # The account-wide risk engine must include both namespace-specific legs.
    from src.core.models import AccountSnapshot
    from src.core.risk import RiskEngine
    hedge = Position("lighter", "UNITREE", "short", 51, 101, 101, 3, None)
    hedge_account = AccountSnapshot("lighter", 1000, 100, 10, [hedge], result.updated_at)
    risk = RiskEngine(0.5, 5, 2000).evaluate([result, hedge_account])
    assert risk.net_delta_usd == 0


def test_configured_and_registered_names_are_additive_deduplicated(monkeypatch):
    connector, calls, _ = setup_connector(monkeypatch, configured="xyz, xyz, legacy,para,legacy",
        registry=[None, {"name": "xyz"}, {"name": "para"}, {"name": "para"}])
    result = asyncio.run(connector.fetch_account_snapshot())
    dexes = [c.get("dex", "") for c in calls if c["type"] == "clearinghouseState"]
    assert sorted(dexes) == ["", "legacy", "para", "xyz"]
    assert result.equity_usd == 1200
    assert result.available_margin_usd == 120
    assert sum(p.size for p in result.positions) == 51


@pytest.mark.parametrize("registry", [{"error": "unavailable"}, [], [None, {}], [None, {"name": None}],
    [None, {"name": ""}], [None, {"name": " para "}], [None, None], ["xyz"]])
def test_bad_discovery_fails_closed(monkeypatch, registry):
    connector, _, _ = setup_connector(monkeypatch, registry=registry)
    with pytest.raises(rc.RealConnectorRequestError, match="perpDexs"):
        asyncio.run(connector.fetch_account_snapshot())


@pytest.mark.parametrize("failure", [("perpDexs", ""), ("clearinghouseState", "para"),
                                     ("metaAndAssetCtxs", "para")])
def test_discovery_or_relevant_dex_failure_never_returns_partial_snapshot(monkeypatch, failure):
    connector, _, _ = setup_connector(monkeypatch, failure=failure)
    accounts, statuses = asyncio.run(MonitoringService([connector]).collect_with_status())
    assert accounts == []
    assert len(statuses) == 1 and not statuses[0].ok


@pytest.mark.parametrize("state", [{"error": "dex unavailable"}, {"marginSummary": {}, "assetPositions": None}])
def test_error_shaped_dex_state_fails_closed(monkeypatch, state):
    connector, _, states = setup_connector(monkeypatch)
    states["para"] = state
    with pytest.raises(rc.RealConnectorRequestError, match="clearinghouseState"):
        asyncio.run(connector.fetch_account_snapshot())


def test_many_dexes_use_bounded_concurrency_and_skip_empty_metadata(monkeypatch):
    registry = [None] + [{"name": f"dex{i}"} for i in range(10)]
    connector, calls, _ = setup_connector(monkeypatch, configured="xyz", registry=registry)
    original = connector._post
    active = peak = 0

    async def delayed(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        try:
            await asyncio.sleep(0.005)
            return await original(**kwargs)
        finally:
            active -= 1

    connector._post = delayed
    asyncio.run(connector.fetch_account_snapshot())
    assert 1 < peak <= 4
    assert len([c for c in calls if c["type"] == "clearinghouseState"]) == 12
    assert [c.get("dex") for c in calls if c["type"] == "metaAndAssetCtxs"] == ["xyz"]
    assert active == 0


def test_native_dex_is_included_even_with_named_configuration(monkeypatch):
    connector, _, states = setup_connector(monkeypatch)
    states[""] = {**deepcopy(EMPTY), "marginSummary": {"accountValue": "30"}, "withdrawable": "5"}
    result = asyncio.run(connector.fetch_account_snapshot())
    assert result.equity_usd == 1230
    assert result.available_margin_usd == 125


def test_discovery_refreshes_on_each_snapshot(monkeypatch):
    connector, calls, _ = setup_connector(monkeypatch)
    original = connector._post
    registries = [[None, {"name": "xyz"}], [None, {"name": "xyz"}, {"name": "para"}]]

    async def post(**kwargs):
        if kwargs["body"]["type"] == "perpDexs":
            return registries.pop(0)
        return await original(**kwargs)

    connector._post = post

    async def collect_twice():
        first = await connector.fetch_account_snapshot()
        second = await connector.fetch_account_snapshot()
        return first, second

    first, second = asyncio.run(collect_twice())
    assert sum(p.size for p in first.positions) == 41
    assert sum(p.size for p in second.positions) == 51
    assert registries == []


def test_portfolio_equity_and_available_are_not_multiplied_by_discovered_dexes(monkeypatch):
    connector, _, _ = setup_connector(monkeypatch)
    original = connector._post

    async def post(**kwargs):
        if kwargs["body"]["type"] == "spotClearinghouseState":
            return {"portfolioMarginEnabled": True,
                    "balances": [{"token": 0, "total": "1500"}],
                    "tokenToAvailableAfterMaintenance": [[0, "321.125"]]}
        return await original(**kwargs)

    connector._post = post
    result = asyncio.run(connector.fetch_account_snapshot())
    assert result.equity_usd == 1500
    assert result.available_margin_usd == 321.125
    assert result.maintenance_margin_usd == 12
    assert sum(p.size for p in result.positions) == 51


@pytest.mark.parametrize("mode", ["failure", "cancel"])
def test_failed_or_cancelled_snapshot_drains_outstanding_reads(monkeypatch, mode):
    connector, _, _ = setup_connector(monkeypatch)
    original = connector._post
    active = 0
    blocked = None

    async def post(**kwargs):
        nonlocal active
        body = kwargs["body"]
        if body["type"] == "perpDexs":
            return await original(**kwargs)
        if mode == "failure" and body.get("dex") == "para":
            raise rc.RealConnectorRequestError("synthetic failure after other DEX started")
        active += 1
        blocked.set()
        try:
            await asyncio.Event().wait()
        finally:
            active -= 1

    connector._post = post

    async def collect():
        nonlocal blocked
        blocked = asyncio.Event()
        task = asyncio.create_task(connector.fetch_account_snapshot())
        await blocked.wait()
        if mode == "cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            with pytest.raises(rc.RealConnectorRequestError):
                await task
        assert active == 0

    asyncio.run(collect())
