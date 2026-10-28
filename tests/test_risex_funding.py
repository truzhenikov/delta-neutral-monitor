"""Synthetic contracts plus sanitized genuine Rise funding record shapes."""
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.funding_report_service import FundingReportService, MSK

BASE = 'https://api.rise.trade'
ACCOUNT = '0x' + 'a' * 40
NOW = datetime(2026, 10, 28, 12, tzinfo=MSK)
START, END = FundingReportService.completed_window(NOW)
LOW, HIGH = 1793089800000000000, 1793176200000000000


def record(**changes):
    # Genuine nonzero public ledger shape, no private/account identifiers.
    result = dict(market_id='20', funding_payment='-0.22354902549461375',
                  position_size='50', block_number='0', block_time='1793174400000000000',
                  tx_hash='', side='long', funding_rate='-0.000040219721350357',
                  is_settled=False, funding_rate_8h='', trade_side='', entry_type='funding')
    result.update(changes)
    return result


def page(*rows, number=1, more=False, account=ACCOUNT):
    return dict(data=dict(account=account, records=list(rows), page=number, has_next_page=more))


def markets(quote='USDC'):
    return dict(data=dict(markets=[dict(market_id='20', display_name='BZ/USDC',
                                      quote_asset_symbol=quote)]))


def collect(request, refs=None, profiles=None, start=START, end=END, base=BASE):
    from src.services.risex_funding import fetch_risex_funding
    profiles = profiles or {'risex:main': dict(account=ACCOUNT)}
    store = SimpleNamespace(get_exchange_credentials=lambda ref: profiles[ref])
    return asyncio.run(fetch_risex_funding(store, refs or list(profiles), start, end, request, base))


def test_missing_implementation_has_real_contract():
    request = AsyncMock(side_effect=[markets(), page(record())])
    assert collect(request) == {'BZ': Decimal('0.22354902549461375')}
    call = request.call_args_list[1]
    assert call.args == ('GET', BASE + '/v1/account/funding-payments')
    assert call.kwargs['params'] == dict(account=ACCOUNT, start_time=LOW, end_time=HIGH - 1,
                                        page=1, limit=1000, sorted_by='-time')
    assert call.kwargs['follow_redirects'] is False


def test_settled_closed_and_hourly_rows_exact_signed_half_open():
    settled = record(block_time=str(LOW), funding_payment='123456789012345678901234567890.123456789',
                     is_settled=True, block_number='18057311', tx_hash='0x' + 'b' * 64)
    request = AsyncMock(side_effect=[markets(), page(settled,
        record(block_time=str(HIGH - 1), funding_payment='-0.000000001'),
        record(block_time=str(HIGH), funding_payment='-999'),
        record(block_time=str(LOW - 1), funding_payment='-999'),
        record(block_time=str(LOW + 1), entry_type='dividend', funding_payment='-999'))])
    with localcontext() as ctx:
        ctx.prec = 3
        assert collect(request) == {'BZ': Decimal('-123456789012345678901234567890.123456788')}
    assert all('positions' not in c.args[1] for c in request.call_args_list)


def test_pages_overlap_dedup_aliases_and_complete_empty():
    profiles = {'risex:one': dict(account=ACCOUNT), 'risex:alias': dict(account=ACCOUNT.upper().replace('0X', '0x'))}
    request = AsyncMock(side_effect=[markets(), page(record(), more=True),
                                    page(record(), record(block_time=str(LOW)), number=2)])
    assert collect(request, profiles=profiles) == {'BZ': Decimal('0.44709805098922750')}
    assert request.await_count == 3
    assert request.call_args_list[-1].kwargs['params']['page'] == 2
    assert collect(AsyncMock(side_effect=[markets(), page()])) == {}


@pytest.mark.parametrize('identity', ['', '0x123', ACCOUNT + ' ', None])
def test_preflight_every_profile_before_network(identity):
    request = AsyncMock()
    with pytest.raises(ValueError):
        collect(request, profiles={'risex:one': dict(account=ACCOUNT), 'risex:bad': dict(account=identity)})
    request.assert_not_awaited()


@pytest.mark.parametrize('bad', [
    {}, {'code': 3, 'message': 'sensitive'},
    dict(page(), success=False), dict(page(), status='error'),
    dict(page(), error={'message': 'sensitive'}),
    dict(data=dict(page()['data'], code=3)),
    page(account='0x' + 'c' * 40), page(number=2), page(more=1),
    page(record(funding_payment='NaN')), page(record(funding_payment=0.2)),
    page(record(block_time='1793174400')), page(record(block_time=1793174400000000000)),
    page(record(is_settled='false')), page(record(entry_type='unknown')),
    page(record(market_id='999')), page(record(side='unknown')),
])
def test_invalid_payload_unknown_not_zero(bad):
    with pytest.raises(ValueError):
        collect(AsyncMock(side_effect=[markets(), bad]))


def test_conflicting_overlap_and_nonadvancing_pages_fail():
    for second in [page(record(funding_payment='-2'), number=2), page(record(), number=2, more=True)]:
        with pytest.raises(ValueError):
            collect(AsyncMock(side_effect=[markets(), page(record(), more=True), second]))


@pytest.mark.parametrize('base,start,end', [
    ('https://evil.invalid', START, END), (BASE, START.replace(tzinfo=None), END),
    (BASE, END, START), (BASE, START - timedelta(days=365 * 100), END),
])
def test_invalid_origin_window_no_network(base, start, end):
    request = AsyncMock()
    with pytest.raises(ValueError):
        collect(request, base=base, start=start, end=end)
    request.assert_not_awaited()


def test_live_deprecated_market_label_still_resolves_closed_history():
    metadata = markets()
    metadata['data']['markets'].append(dict(market_id='13',
        display_name='DOGE/USDC [deprecated-1779958099]', quote_asset_symbol='USDC', active=False))
    request = AsyncMock(side_effect=[metadata, page(record(market_id='13'))])
    assert collect(request) == {'DOGE': Decimal('0.22354902549461375')}


def test_terminal_repeated_page_cannot_claim_complete_history():
    request = AsyncMock(side_effect=[markets(), page(record(), more=True),
                                    page(record(), number=2, more=False)])
    with pytest.raises(ValueError, match='no progress'):
        collect(request)


def test_settlement_transition_same_period_fails_instead_of_double_counting():
    settled = record(is_settled=True, block_number='18057311', tx_hash='0x' + 'b' * 64)
    with pytest.raises(ValueError, match='conflicting Rise funding period'):
        collect(AsyncMock(side_effect=[markets(), page(record(), more=True),
                                      page(settled, number=2)]))


def test_bad_currency_and_budget(monkeypatch):
    with pytest.raises(ValueError):
        collect(AsyncMock(side_effect=[markets('ETH')]))
    import src.services.risex_funding as funding
    monkeypatch.setattr(funding, 'MAX_PAGES', 1)
    with pytest.raises(ValueError):
        collect(AsyncMock(side_effect=[markets(), page(record(), more=True)]))
