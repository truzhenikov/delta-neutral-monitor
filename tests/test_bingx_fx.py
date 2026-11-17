"""Sanitized native USDJPY payload from the authenticated positions endpoint."""
import asyncio

import pytest

from src.connectors.real_connectors import RealConnectorRequestError
from src.core.models import Position
from tests.test_bingx_connector import StubBingxConnector

USDJPY = {
    'symbol': 'NCFXUSD2JPY-USDT', 'positionAmt': '8519.98',
    'positionSide': 'LONG', 'avgPrice': '159.366', 'markPrice': '159.400',
    'initialMargin': '85.2338', 'leverage': 100, 'unrealizedProfit': '1.7901',
    'positionValue': '8525.16', 'liquidationPrice': '100.522', 'currency': 'USDT',
}


def snapshot(monkeypatch, rows):
    connector = StubBingxConnector()
    monkeypatch.setattr(connector, '_build_signed_params', lambda: ({}, {}))
    connector._responses = [
        {'code': 0, 'data': {'balance': {'equity': '1000'}}},
        {'code': 0, 'data': rows},
    ]
    return asyncio.run(connector.fetch_account_snapshot())


def test_native_usdjpy_values_and_mark_equivalent_units(monkeypatch):
    position = snapshot(monkeypatch, [USDJPY]).positions[0]
    assert position.notional_usd == pytest.approx(8525.16)
    assert position.pnl_usd == pytest.approx(1.7901)
    assert position.size == pytest.approx(8525.16 / 159.4)
    assert position.symbol == 'USDJPY-USDT'
    assert position.liquidation_price == 100.522
    # Same quote-price USD-risk units as the Ondo synthetic, not 8519 USD base units.
    hedge = Position('ondo', 'USDJPY-USD.P', 'short', 53.51, 159, 159.322, 100, 177)
    assert abs(position.delta_usd + hedge.delta_usd) < 1

    # Core aggregators and the public API must consume native valuation, not
    # recompute PnL from the synthetic mark-equivalent quantity.
    from src.core.risk import RiskEngine
    from src.services.status_service import StatusService
    account = snapshot(monkeypatch, [USDJPY])
    account.positions.append(hedge)
    status = StatusService(RiskEngine(0.9, 4, 2000)).build_status([account])
    assert abs(status.risk.net_delta_usd) < 1
    assert status.accounts[0].positions[0].pnl_usd == pytest.approx(1.7901)
    assert status.accounts[0].total_notional_usd == pytest.approx(8525.16 + hedge.notional_usd)
    assert status.risk.min_liq_distance_pct == pytest.approx((177 - 159.322) / 159.322 * 100)


@pytest.mark.parametrize('side,sign', [('LONG', 1), ('SHORT', -1)])
def test_usdjpy_native_pnl_and_notional_sign(monkeypatch, side, sign):
    position = snapshot(monkeypatch, [dict(USDJPY, positionSide=side, unrealizedProfit='0')]).positions[0]
    assert position.delta_usd == pytest.approx(sign * 8525.16)
    assert position.pnl_usd == 0


@pytest.mark.parametrize('field,bad', [
    ('markPrice', '0'), ('markPrice', '-1'), ('markPrice', 'NaN'),
    ('markPrice', 'Infinity'), ('markPrice', None), ('markPrice', 'bad'),
    ('positionValue', None), ('positionValue', 'NaN'), ('positionValue', '-1'),
    ('positionValue', '0'), ('unrealizedProfit', None), ('unrealizedProfit', 'inf'),
    ('currency', 'JPY'), ('avgPrice', 'NaN'), ('positionSide', 'BOTH'),
    ('positionAmt', 'NaN'), ('positionAmt', '-1'),
    ('symbol', 'NCFXEUR2JPY-USDT'),
])
def test_fx_fails_closed_for_ambiguous_units(monkeypatch, field, bad):
    with pytest.raises(RealConnectorRequestError, match='BingX FX'):
        snapshot(monkeypatch, [dict(USDJPY, **{field: bad})])


def test_crypto_and_silver_retain_native_base_quantities(monkeypatch):
    crypto = dict(USDJPY, symbol='ETH-USDT', positionAmt='2.5', avgPrice='3000',
                  markPrice='3100', positionValue='7750', unrealizedProfit='250')
    silver = dict(USDJPY, symbol='NCCOXAG2USD-USDT', positionAmt='240',
                  avgPrice='59.10', markPrice='47.66', positionSide='SHORT',
                  positionValue='11439.19', unrealizedProfit='2745.6')
    a, b = snapshot(monkeypatch, [crypto, silver]).positions
    assert (a.size, a.notional_usd, a.pnl_usd) == (2.5, 7750, 250)
    assert b.size == 240
    assert b.notional_usd == pytest.approx(240 * 47.66)
    assert b.pnl_usd == pytest.approx(240 * (59.10 - 47.66))


def test_fx_flat_row_does_not_require_a_mark(monkeypatch):
    assert snapshot(monkeypatch, [dict(USDJPY, positionAmt='0', markPrice='0')]).positions == []
