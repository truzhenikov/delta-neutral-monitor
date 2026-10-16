import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import MSK, FundingReportService, render_funding_report

BASE = 'https://api.arcus.xyz'
ADDRESS = '0x' + 'ab' * 20
START = datetime(2026, 10, 14, 11, 30, tzinfo=MSK)
END = START + timedelta(milliseconds=4)
LOW = 1791966600000000
NOW = datetime(2026, 10, 16, 3, tzinfo=MSK)


def row(**changes):
    return dict(dict(marketId=1, marketDisplayName='BTC-USD', fundingRate='-0.001',
                     size='-2', payment='1.23456789', time=LOW), **changes)


def page(rows):
    return dict(fundingPayments=rows, total=len(rows))


def store_at(tmp_path, identities=None):
    store = CredentialStore(tmp_path / 'credentials.json')
    for i, identity in enumerate(identities or [dict(address=ADDRESS, account_index='0')]):
        store.set_exchange_credentials(f'arcus:p{i}', identity)
    return store


def run(tmp_path, request, identities=None, start=START, end=END, base=BASE):
    module = importlib.import_module('src.services.arcus_funding')
    identities = identities or [dict(address=ADDRESS, account_index='0')]
    refs = [f'arcus:p{i}' for i in range(len(identities))]
    public_store = SimpleNamespace(get_exchange_credentials=lambda ref: identities[refs.index(ref)])
    return asyncio.run(module.fetch_arcus_funding(public_store, refs, start, end, request, base))


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    try:
        module = importlib.import_module('src.services.arcus_funding')
    except ModuleNotFoundError:
        return
    monkeypatch.setattr(module, 'sleep', AsyncMock())


def test_public_wire_actual_signed_amount_closed_markets_and_optional_total(tmp_path):
    request = AsyncMock(return_value={'fundingPayments': [row(), row(marketId=2, marketDisplayName='OLD-USD', payment='-2')]})
    assert run(tmp_path, request) == {'BTC': Decimal('1.23456789'), 'OLD': Decimal('-2')}
    assert request.call_args.args == ('GET', BASE + '/v1/funding')
    assert request.call_args.kwargs == {'params': dict(address=ADDRESS, accountIndex=0, limit=1000, **{'from': LOW, 'to': LOW+3000}), 'follow_redirects': False}


def test_exact_addition_with_reduced_decimal_precision(tmp_path):
    request = AsyncMock(return_value=page([row(payment='123456789012345678901234567890.00000001'),
        row(time=LOW+1000, payment='0.00000002')]))
    with localcontext() as context:
        context.prec = 4
        assert run(tmp_path, request)['BTC'] == Decimal('123456789012345678901234567890.00000003')


def test_aliases_canonicalized_distinct_subaccounts_counted_and_paced(tmp_path):
    identities = [dict(address=ADDRESS, account_index='0'), dict(address='0x'+('AB'*20), account_index='0'),
                  dict(address=ADDRESS, account_index='1')]
    request = AsyncMock(return_value=page([row()]))
    assert run(tmp_path, request, identities)['BTC'] == Decimal('2.46913578')
    assert request.await_count == 2
    module = importlib.import_module('src.services.arcus_funding')
    module.sleep.assert_awaited_once_with(0.51)


@pytest.mark.parametrize('identity', [dict(address=ADDRESS), dict(account_index='0'),
    dict(address=ADDRESS+' ', account_index=0), dict(address=ADDRESS, account_index=True),
    dict(address=ADDRESS, account_index='00'), dict(address=ADDRESS, account_index='10'),
    dict(address='0x123', account_index=0)])
def test_all_identities_validated_before_any_get(tmp_path, identity):
    request = AsyncMock(return_value=page([]))
    with pytest.raises(ValueError):
        run(tmp_path, request, [dict(address=ADDRESS, account_index='0'), identity])
    request.assert_not_called()


@pytest.mark.parametrize('changes', [dict(marketId=True), dict(marketId=65536), dict(marketId=-1),
    dict(marketDisplayName='BTC-USDT'), dict(marketDisplayName='-USD'), dict(marketDisplayName=None),
    dict(payment='NaN'), dict(payment=1.2), dict(payment=''), dict(size='Infinity'), dict(fundingRate='bad'),
    dict(time=LOW+1), dict(time=str(LOW)), dict(time=True), dict(time=1000), dict(currency='USDT'),
    dict(asset='EUR'), dict(quoteCurrency='BTC')])
def test_bad_record_fails_closed(tmp_path, changes):
    with pytest.raises(ValueError):
        run(tmp_path, AsyncMock(return_value=page([row(**changes)])))


@pytest.mark.parametrize('payload', [[], {}, {'fundingPayments': None}, {'fundingPayments': [], 'total': 1},
    {'fundingPayments': [], 'total': False}, {'fundingPayments': [], 'success': False},
    {'fundingPayments': [], 'error': 'denied'}, {'fundingPayments': [], 'code': 'FAILED'}])
def test_bad_response_fails_closed(tmp_path, payload):
    with pytest.raises(ValueError):
        run(tmp_path, AsyncMock(return_value=payload))


def test_identical_events_deduplicated_conflicts_rejected(tmp_path):
    assert run(tmp_path, AsyncMock(return_value=page([row(), row()]))) == {'BTC': Decimal('1.23456789')}
    with pytest.raises(ValueError, match='conflict'):
        run(tmp_path, AsyncMock(return_value=page([row(), row(payment='2')])))


def test_saturated_parent_split_on_disjoint_millisecond_ticks_not_counted(tmp_path):
    calls = []

    async def request(method, url, **kwargs):
        p = kwargs['params']
        calls.append((p['from'], p['to']))
        if len(calls) == 1:
            return page([row(marketId=i, payment='2') for i in range(1000)])
        return page([row(time=p['from'], payment='2')])

    assert run(tmp_path, request) == {'BTC': Decimal('4')}
    assert calls == [(LOW, LOW+3000), (LOW, LOW+1000), (LOW+2000, LOW+3000)]


def test_parent_child_conflict_rejected(tmp_path):
    request = AsyncMock(side_effect=[page([row(marketId=i) for i in range(1000)]), page([row(payment='9')])])
    with pytest.raises(ValueError, match='conflict'):
        run(tmp_path, request)


def test_saturated_single_millisecond_fails_closed(tmp_path):
    with pytest.raises(ValueError, match='saturated'):
        run(tmp_path, AsyncMock(return_value=page([row(marketId=i) for i in range(1000)])), end=START+timedelta(milliseconds=1))


def test_request_budget_is_global_across_profiles(tmp_path, monkeypatch):
    module = importlib.import_module('src.services.arcus_funding')
    monkeypatch.setattr(module, 'MAX_REQUESTS', 2)
    request = AsyncMock(return_value=page([row(marketId=i) for i in range(1000)]))
    with pytest.raises(ValueError, match='budget'):
        run(tmp_path, request)
    assert request.await_count == 2


def test_fractional_boundaries_use_exact_tick_ceiling(tmp_path):
    request = AsyncMock(return_value=page([]))
    run(tmp_path, request, start=START+timedelta(microseconds=1), end=START+timedelta(microseconds=3001))
    assert request.call_args.kwargs['params']['from'] == LOW+1000
    assert request.call_args.kwargs['params']['to'] == LOW+3000


def test_no_ticks_empty_without_request(tmp_path):
    request = AsyncMock()
    assert run(tmp_path, request, start=START+timedelta(microseconds=1), end=START+timedelta(microseconds=999)) == {}
    request.assert_not_called()


@pytest.mark.parametrize('start,end,base', [(START.replace(tzinfo=None), END, BASE), (END, START, BASE),
    (START, END, BASE+'/evil'), (datetime(1970, 1, 1, tzinfo=timezone.utc), END, BASE)])
def test_invalid_window_origin_rejected_without_request(tmp_path, start, end, base):
    request = AsyncMock()
    with pytest.raises(ValueError):
        run(tmp_path, request, start=start, end=end, base=base)
    request.assert_not_called()


@pytest.mark.parametrize('failure', ['profile', 'split'])
def test_builder_withholds_entire_arcus_after_partial_success_preserves_hyperliquid(tmp_path, failure):
    store = store_at(tmp_path, [dict(address=ADDRESS, account_index='0'), dict(address=ADDRESS, account_index='1')]
                     if failure == 'profile' else None)
    store.set_exchange_credentials('hyperliquid', dict(user_address='synthetic'))
    calls = []

    async def request(method, url, **kwargs):
        if url.endswith('/info'):
            return [dict(time=LOW//1000, delta=dict(coin='ETH', usdc='2'))]
        calls.append(kwargs['params'])
        if failure == 'split' and len(calls) == 1:
            return page([row(marketId=i) for i in range(1000)])
        if len(calls) == (2 if failure == 'split' else 1):
            return page([row()])
        raise RuntimeError('sensitive ' + ADDRESS)

    report = asyncio.run(FundingReportService(store, tmp_path/'state.json', request_json=request).build_report(NOW))
    assert not report.complete
    assert [(r.exchange, r.amount_usd) for r in report.rows] == [('Hyperliquid', Decimal('2'))]
    assert len(calls) == (3 if failure == 'split' else 2)
    assert len(report.warnings) == 1 and 'Arcus' in report.warnings[0]
    assert ADDRESS not in render_funding_report(report)


def test_builder_success_empty_disabled_and_labels(tmp_path):
    store = store_at(tmp_path)
    request = AsyncMock(return_value=page([row()]))
    service = FundingReportService(store, tmp_path/'state.json', request_json=request)
    report = asyncio.run(service.build_report(NOW))
    assert report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [('Arcus', 'BTC', Decimal('1.23456789'))]
    assert 'Arcus — BTC — +1.23 USD' in render_funding_report(report)
    request.return_value = page([])
    assert asyncio.run(service.build_report(NOW)).rows == []
    store.set_exchange_enabled('arcus:p0', False)
    request.reset_mock()
    assert asyncio.run(service.build_report(NOW)).complete
    request.assert_not_called()


def test_dependencies_explicitly_wire_arcus_origin(monkeypatch):
    import src.deps as deps
    fields = ['hyperliquid', 'lighter', 'lighter_rh', 'variational', 'bingx', 'okx', 'ondo', 'aster', 'gate', 'woofi', 'arcus', 'binance', 'bitget']
    settings = SimpleNamespace(**{name+'_api_base': BASE for name in fields}, funding_report_state_path='unused.json',
                               gate_settle_currency='usdt', mexc_funding_api_base='mexc', request_timeout_sec=3)
    monkeypatch.setattr(deps, 'get_settings', lambda: settings)
    monkeypatch.setattr(deps, 'get_credential_store', lambda: None)
    deps.get_funding_report_service.cache_clear()
    try:
        assert deps.get_funding_report_service().arcus_api_base == BASE
    finally:
        deps.get_funding_report_service.cache_clear()


@pytest.mark.parametrize('extra', [dict(status='error'), dict(status='FAILED'),
    dict(currency='EUR'), dict(asset='USDT'), dict(quoteCurrency='USDC')])
def test_response_business_failure_or_contradictory_currency_rejected(tmp_path, extra):
    with pytest.raises(ValueError):
        run(tmp_path, AsyncMock(return_value=dict(page([row()]), **extra)))


def test_all_market_ties_partition_without_omission_or_parent_double_count(tmp_path):
    ledger = [row(marketId=market, time=LOW+tick*1000, payment='0.1')
              for tick in range(7) for market in range(150)]
    calls = []

    async def request(method, url, **kwargs):
        p = kwargs['params']
        calls.append(p)
        matches = [item for item in reversed(ledger) if p['from'] <= item['time'] <= p['to']]
        return page(matches[:p['limit']])

    assert run(tmp_path, request, end=START+timedelta(milliseconds=7)) == {'BTC': Decimal('105.0')}
    assert len(calls) == 3
    assert calls[1]['to']+1000 == calls[2]['from']


def test_budget_spans_distinct_profiles(tmp_path, monkeypatch):
    module = importlib.import_module('src.services.arcus_funding')
    assert module.MAX_REQUESTS == 256
    monkeypatch.setattr(module, 'MAX_REQUESTS', 2)
    identities = [dict(address=ADDRESS, account_index=str(i)) for i in range(3)]
    request = AsyncMock(return_value=page([row()]))
    with pytest.raises(ValueError, match='budget'):
        run(tmp_path, request, identities)
    assert request.await_count == 2


@pytest.mark.parametrize('time', [LOW-1000, LOW+4000])
def test_api_violating_requested_interval_fails_closed(tmp_path, time):
    with pytest.raises(ValueError):
        run(tmp_path, AsyncMock(return_value=page([row(time=time)])))
