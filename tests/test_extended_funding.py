import asyncio
import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import MSK, FundingReportService, render_funding_report

BASE = 'https://api.starknet.extended.exchange'
START = datetime(2026, 10, 15, 11, 30, tzinfo=MSK)
END = START + timedelta(days=1)
NOW = END + timedelta(minutes=1)
LOW = 1792053000000
KEY = 'test-only-api-key'


def row(**changes):
    return dict(dict(id=209088906, accountId=282422, market='NVDA_24_5-USD',
                     positionId=2100941462010068992, side='LONG', size='45',
                     value='10025.234999', markPrice='222.782999999999',
                     fundingFee='-0.040101', fundingRate='0.000004', paidTime=LOW), **changes)


def page(rows, cursor=None):
    return dict(status='OK', data=rows, pagination=dict(count=len(rows), **({} if cursor is None else {'cursor': cursor})))


def run(request, identities=None, base=BASE, start=START, end=END):
    module = importlib.import_module('src.services.extended_funding')
    identities = identities or [dict(api_key=KEY)]
    refs = [f'extended:p{i}' for i in range(len(identities))]
    store = SimpleNamespace(get_exchange_credentials=lambda ref: identities[refs.index(ref)])
    return asyncio.run(module.fetch_extended_funding(store, refs, start, end, request, base))


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    try:
        module = importlib.import_module('src.services.extended_funding')
    except ModuleNotFoundError:
        return
    monkeypatch.setattr(module, 'sleep', AsyncMock())


def test_actual_signed_fee_not_reconstructed_and_closed_market_name_preserved():
    request = AsyncMock(return_value=page([row(), row(id=2, market='BTC-USD', fundingFee='2', side='SHORT')]))
    assert run(request) == {'NVDA_24_5': Decimal('-0.040101'), 'BTC': Decimal('2')}
    assert request.call_args.args == ('GET', BASE+'/api/v1/user/funding/history')
    assert request.call_args.kwargs == dict(params=dict(startTime=LOW, limit=100),
                                           headers={'X-Api-Key': KEY, 'User-Agent': 'delta-neutral-monitor'},
                                           follow_redirects=False)


def test_walks_short_and_empty_pages_to_explicit_end_and_exact_half_open_window():
    request = AsyncMock(side_effect=[page([row(id=1, paidTime=LOW+86400000)], 99), page([], 98),
                                    page([row(id=2, paidTime=LOW-1), row(id=3), row(id=4, paidTime=LOW+1)], 97),
                                    page([])])
    assert run(request, start=START+timedelta(microseconds=1)) == {'NVDA_24_5': Decimal('-0.040101')}
    assert [c.kwargs['params'].get('cursor') for c in request.call_args_list] == [None, 99, 98, 97]
    assert all(c.kwargs['params']['startTime'] == LOW for c in request.call_args_list)


def test_more_than_ten_thousand_records_not_truncated():
    pages = [page([row(id=i*100+j) for j in range(100)], 999999-i) for i in range(101)]
    request = AsyncMock(side_effect=pages+[page([])])
    assert run(request) == {'NVDA_24_5': Decimal('-405.020100')}
    assert request.await_count == 102


def test_exact_money_and_overlap_deduplication_even_under_low_decimal_precision():
    first = row(fundingFee='123456789012345678901234567890.00000001')
    request = AsyncMock(side_effect=[page([first], 22), page([first, row(id=2, fundingFee='0.00000002')])])
    with localcontext() as context:
        context.prec = 4
        assert run(request) == {'NVDA_24_5': Decimal('123456789012345678901234567890.00000003')}


def test_api_key_aliases_and_account_identity_deduplicate_but_distinct_accounts_add():
    request = AsyncMock(side_effect=[page([row()]), page([row()]), page([row(accountId=999)])])
    assert run(request, [dict(api_key=KEY), dict(api_key=KEY), dict(api_key='alias'), dict(api_key='other')]) == {
        'NVDA_24_5': Decimal('-0.080202')}
    assert request.await_count == 3
    module = importlib.import_module('src.services.extended_funding')
    assert module.sleep.await_count == 2
    assert all(c.args == (0.51,) for c in module.sleep.call_args_list)


@pytest.mark.parametrize('bad', ['', None, 42, ' leading', 'trailing ', 'bad\r\nkey'])
def test_missing_or_unsafe_key_fails_before_any_request(bad):
    request = AsyncMock()
    with pytest.raises(ValueError, match='api_key'):
        run(request, [dict(api_key=KEY), dict(api_key=bad)])
    request.assert_not_called()


@pytest.mark.parametrize('base', ['http://api.starknet.extended.exchange', BASE+'/evil', BASE+'@evil.test',
                                 BASE+'?a=b', 'https://evil.test'])
def test_origin_rejected_before_credentials_sent(base):
    request = AsyncMock()
    with pytest.raises(ValueError):
        run(request, base=base)
    request.assert_not_called()


@pytest.mark.parametrize('start,end', [(START.replace(tzinfo=None), END), (END, START),
                                      (datetime(1970, 1, 1, tzinfo=timezone.utc), END)])
def test_invalid_window_no_network(start, end):
    request = AsyncMock()
    with pytest.raises(ValueError):
        run(request, start=start, end=end)
    request.assert_not_called()


@pytest.mark.parametrize('changes', [dict(id=None), dict(id=True), dict(id=-1), dict(accountId=None),
    dict(accountId=True), dict(positionId=None), dict(side='long'), dict(market='BTC-USDT'),
    dict(market='-USD'), dict(market='BTC\n-USD'), dict(paidTime=LOW//1000), dict(paidTime=LOW*1000),
    dict(paidTime=str(LOW)), dict(paidTime=float(LOW)), dict(paidTime=True), dict(fundingFee='NaN'),
    dict(fundingFee=0.1), dict(fundingFee=None), dict(fundingRate='Infinity'), dict(markPrice='bad'),
    dict(value=''), dict(size='bad'), dict(currency='EUR')])
def test_bad_records_fail_closed(changes):
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=page([row(**changes)])))


@pytest.mark.parametrize('payload', [{}, [], dict(status='ERROR', data=[], pagination=dict(count=0)),
    dict(status='OK', data=[]), dict(status='OK', data=None, pagination=dict(count=0)),
    dict(status='OK', data=[], pagination=dict(count=1)), dict(status='OK', data=[], pagination=dict(count=True)),
    dict(status='OK', data=[], pagination=dict(count=0, cursor='123')),
    dict(status='OK', data=[], pagination=dict(count=0, cursor=True)),
    dict(status='OK', data=[], pagination=dict(count=0, cursor=-1)),
    dict(status='OK', data=[], pagination=dict(count=0), error=dict(code=1)), page([row()]*101)])
def test_bad_pages_fail_closed(payload):
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=payload))


@pytest.mark.parametrize('changes', [dict(fundingFee='99'), dict(market='BTC-USD'), dict(paidTime=LOW+1)])
def test_conflicting_event_rejected(changes):
    with pytest.raises(ValueError, match='conflict'):
        run(AsyncMock(side_effect=[page([row()], 2), page([row(**changes)])]))


def test_mixed_account_within_profile_rejected():
    with pytest.raises(ValueError, match='account'):
        run(AsyncMock(side_effect=[page([row()], 2), page([row(id=2, accountId=999)])]))


def test_repeated_cursor_and_nonprogressing_records_rejected():
    with pytest.raises(ValueError, match='cursor'):
        run(AsyncMock(side_effect=[page([], 22), page([], 22)]))
    with pytest.raises(ValueError, match='progress'):
        run(AsyncMock(side_effect=[page([row()], 22), page([row()], 21)]))


def test_sdk_nullable_optional_fields_are_successful_empty_history():
    payload = dict(status='OK', data=[], pagination=dict(count=0, cursor=None), error=None)
    assert run(AsyncMock(return_value=payload)) == {}


def test_request_budget_global_across_profiles(monkeypatch):
    module = importlib.import_module('src.services.extended_funding')
    assert module.MAX_REQUESTS == 256
    monkeypatch.setattr(module, 'MAX_REQUESTS', 2)
    request = AsyncMock(side_effect=[page([], 22), page([])])
    with pytest.raises(ValueError, match='budget'):
        run(request, [dict(api_key=KEY), dict(api_key='other')])
    assert request.await_count == 2


def test_report_enabled_actual_history_and_disabled_gating_and_complete_empty(tmp_path):
    store = CredentialStore(tmp_path/'credentials.json')
    store.set_exchange_credentials('extended:main', dict(api_key=KEY))
    store.set_exchange_credentials('extended:disabled', dict(api_key='disabled-secret'))
    store.set_exchange_enabled('extended:disabled', False)
    request = AsyncMock(return_value=page([row()]))
    service = FundingReportService(store, tmp_path/'state.json', request_json=request)
    report = asyncio.run(service.build_report(NOW))
    assert report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [('Extended', 'NVDA_24_5', Decimal('-0.040101'))]
    assert request.await_count == 1
    request.return_value = page([])
    report = asyncio.run(service.build_report(NOW))
    assert report.complete and not report.rows and not report.warnings
    store.set_exchange_enabled('extended:main', False)
    request.reset_mock()
    assert asyncio.run(service.build_report(NOW)).complete
    request.assert_not_called()


@pytest.mark.parametrize('failure', ['page', 'profile', 'missing_key'])
def test_report_withholds_all_extended_after_later_failure_and_preserves_other_venue(tmp_path, failure):
    store = CredentialStore(tmp_path/'credentials.json')
    store.set_exchange_credentials('extended:main', dict(api_key=KEY))
    if failure in ('profile', 'missing_key'):
        store.set_exchange_credentials('extended:second', dict(api_key='second' if failure == 'profile' else '', client_id='unused'))
    store.set_exchange_credentials('hyperliquid', dict(user_address='0x'+'12'*20))
    calls = []

    async def request(method, url, **kwargs):
        if url.endswith('/info'):
            return [dict(time=LOW, delta=dict(coin='BTC', usdc='3'))]
        calls.append(kwargs)
        if len(calls) == 1:
            return page([row()], 1 if failure == 'page' else None)
        raise RuntimeError(KEY)

    report = asyncio.run(FundingReportService(store, tmp_path/'state.json', request_json=request).build_report(NOW))
    assert not report.complete
    assert [(r.exchange, r.amount_usd) for r in report.rows] == [('Hyperliquid', Decimal('3'))]
    assert len(report.warnings) == 1 and report.warnings[0].startswith('Extended —')
    assert KEY not in render_funding_report(report)
    if failure == 'missing_key':
        assert 'api_key' in report.warnings[0]
        assert not calls


@pytest.mark.parametrize('redirect', [False, True])
def test_real_http_sends_only_api_key_user_agent_and_never_follows_redirects(tmp_path, monkeypatch, redirect):
    import httpx
    import src.services.funding_report_service as reports
    seen = []

    def handle(request):
        seen.append(request)
        assert request.url.path == '/api/v1/user/funding/history'
        assert dict(request.url.params) == dict(startTime=str(LOW), limit='100')
        assert request.headers['X-Api-Key'] == KEY
        assert request.headers['User-Agent'] == 'delta-neutral-monitor'
        assert 'Authorization' not in request.headers
        assert 'must-not-send' not in str(request.headers)
        if redirect:
            return httpx.Response(302, headers={'Location': 'https://evil.invalid/stolen'})
        return httpx.Response(200, json=page([row()]))

    real_client = httpx.AsyncClient
    monkeypatch.setattr(reports.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw))
    store = CredentialStore(tmp_path/'credentials.json')
    store.set_exchange_credentials('extended:main', dict(api_key=KEY, stark_key_private='must-not-send', client_id='must-not-send'))
    report = asyncio.run(FundingReportService(store, tmp_path/'state.json').build_report(NOW))
    assert len(seen) == 1
    assert report.complete is not redirect
    assert len(report.rows) == (0 if redirect else 1)
    assert KEY not in str(report.warnings)


def test_dependencies_explicitly_wire_extended_origin(monkeypatch):
    import src.deps as deps
    fields = ['hyperliquid', 'lighter', 'lighter_rh', 'variational', 'bingx', 'okx', 'ondo', 'aster', 'gate', 'woofi', 'arcus', 'binance', 'bitget']
    settings = SimpleNamespace(**{name+'_api_base': 'https://example.invalid' for name in fields},
                               extended_api_base=BASE+'/configured', funding_report_state_path='unused.json',
                               gate_settle_currency='usdt', mexc_funding_api_base='mexc', request_timeout_sec=3)
    monkeypatch.setattr(deps, 'get_settings', lambda: settings)
    monkeypatch.setattr(deps, 'get_credential_store', lambda: None)
    deps.get_funding_report_service.cache_clear()
    try:
        assert deps.get_funding_report_service().extended_api_base == BASE+'/configured'
    finally:
        deps.get_funding_report_service.cache_clear()
