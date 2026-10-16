"""Offline contract tests; all credentials and ledger rows are synthetic."""
import asyncio
import base64
import hashlib
import hmac
import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
from unittest.mock import AsyncMock

import httpx
import pytest

BASE = 'https://api.bitget.com'
START = datetime(2026, 10, 15, tzinfo=timezone.utc)
END = START + timedelta(days=1)
NOW = END + timedelta(minutes=1)
LOW = (START - datetime(1970, 1, 1, tzinfo=timezone.utc)) // timedelta(milliseconds=1)
TYPES = ('CONTRACT_MAIN_SETTLE_FEE_USER_IN', 'CONTRACT_MAIN_SETTLE_FEE_USER_OUT',
         'MARGIN_SETTLE_FEE_USER_IN', 'MARGIN_SETTLE_FEE_USER_OUT')
CREDS = dict(api_key='synthetic-key', api_secret='synthetic-secret', api_passphrase='synthetic-phrase')


def row(**changes):
    return dict(dict(id='100', category='USDT-FUTURES', coin='USDT', symbol='BTCUSDT',
                     type=TYPES[0], amount='1.25', fee='-999', ts=str(LOW)), **changes)


def page(rows=(), cursor=''):
    return dict(code='00000', data=dict(list=list(rows), cursor=cursor))


def run(request, identities=None, **kwargs):
    module = importlib.import_module('src.services.bitget_funding')
    identities = [CREDS] if identities is None else identities
    refs = [f'bitget:p{i}' for i in range(len(identities))]
    store = SimpleNamespace(get_exchange_credentials=lambda ref: identities[refs.index(ref)])
    defaults = dict(now=NOW, sleep=AsyncMock())
    defaults.update(kwargs)
    return asyncio.run(module.fetch_bitget_funding(store, refs, START, END, request, BASE, **defaults))


def ledger_request(rows):
    async def request(method, url, **kwargs):
        query = parse_qs(urlsplit(url).query)
        selected = [r for r in rows if r['category'] == query['category'][0] and r['type'] == query['type'][0]]
        return page(selected)
    return AsyncMock(side_effect=request)


def test_signed_actual_amount_including_closed_contracts_and_all_settlement_products():
    request = ledger_request([row(), row(id='101', type=TYPES[1], amount='-0.25'),
                              row(id='102', type=TYPES[2], amount='0.1'),
                              row(id='103', type=TYPES[3], amount='-0.05'),
                              row(id='104', category='USDC-FUTURES', coin='USDC', symbol='ETHUSDC', amount='2')])
    assert run(request) == {'BTC': Decimal('1.05'), 'ETH': Decimal('2')}
    assert request.await_count == 8
    for call in request.call_args_list:
        method, url = call.args
        query = parse_qs(urlsplit(url).query)
        assert method == 'GET'
        assert url.startswith(BASE+'/api/v3/account/financial-records?')
        assert query['startTime'] == [str(LOW)]
        assert query['endTime'] == [str(LOW + 86400000 - 1)]
        assert query['limit'] == ['100']
        assert call.kwargs['follow_redirects'] is False
        headers = call.kwargs['headers']
        wire = httpx.Request(method, url).url
        signed_path = wire.raw_path.decode()
        expected = base64.b64encode(hmac.new(CREDS['api_secret'].encode(),
            (headers['ACCESS-TIMESTAMP']+'GET'+signed_path).encode(), hashlib.sha256).digest()).decode()
        assert headers['ACCESS-SIGN'] == expected
        assert headers['ACCESS-KEY'] == CREDS['api_key']
        assert headers['ACCESS-PASSPHRASE'] == CREDS['api_passphrase']


def test_short_page_cursor_followed_to_empty_with_opaque_encoded_cursor():
    request = AsyncMock(side_effect=[page([row()], 'opaque +/?'), page([], 'next'), page([])] + [page()]*7)
    assert run(request) == {'BTC': Decimal('1.25')}
    call = request.call_args_list[1]
    assert parse_qs(urlsplit(call.args[1]).query)['cursor'] == ['opaque +/?']
    wire = httpx.Request(*call.args).url
    assert b'cursor=opaque+%2B%2F%3F' in wire.query
    headers = call.kwargs['headers']
    expected = base64.b64encode(hmac.new(
        CREDS['api_secret'].encode(),
        (headers['ACCESS-TIMESTAMP'] + 'GET' + wire.raw_path.decode()).encode(),
        hashlib.sha256,
    ).digest()).decode()
    assert headers['ACCESS-SIGN'] == expected
    assert request.await_count == 10


def test_exact_money_low_precision_and_half_open_boundaries():
    request = ledger_request([row(amount='123456789012345678901234567890.00000001'),
                              row(id='2', amount='0.00000002'),
                              row(id='3', ts=str(LOW-1), amount='100'),
                              row(id='4', ts=str(LOW+86400000), amount='100')])
    with localcontext() as ctx:
        ctx.prec = 4
        assert run(request) == {'BTC': Decimal('123456789012345678901234567890.00000003')}


def test_profile_alias_dedup_but_distinct_accounts_same_id_both_count():
    request = ledger_request([row()])
    assert run(request, [CREDS, CREDS.copy(), dict(CREDS, api_key='other')]) == {'BTC': Decimal('2.50')}
    assert request.await_count == 16


def test_conflicting_alias_secret_rejected_before_any_request():
    request = AsyncMock(return_value=page())
    with pytest.raises(ValueError, match='conflicting Bitget profile credentials') as caught:
        run(request, [CREDS, dict(CREDS, api_secret='different-synthetic-secret')])
    request.assert_not_awaited()
    assert all(value not in str(caught.value) for value in CREDS.values())
    assert 'different-synthetic-secret' not in str(caught.value)


def test_conflicting_alias_passphrase_rejected_before_any_request():
    request = AsyncMock(return_value=page())
    with pytest.raises(ValueError, match='conflicting Bitget profile credentials') as caught:
        run(request, [CREDS, dict(CREDS, api_passphrase='different-synthetic-phrase')])
    request.assert_not_awaited()
    assert all(value not in str(caught.value) for value in CREDS.values())
    assert 'different-synthetic-phrase' not in str(caught.value)


@pytest.mark.parametrize('field', ['api_key', 'api_secret', 'api_passphrase'])
@pytest.mark.parametrize('same_key', [False, True])
def test_later_missing_credentials_rejected_before_any_request(field, same_key):
    request = AsyncMock(return_value=page())
    later = dict(CREDS, api_key=CREDS['api_key'] if same_key else 'other-synthetic-key')
    later.pop(field)
    with pytest.raises(ValueError, match='missing Bitget profile credentials'):
        run(request, [CREDS, CREDS.copy(), later])
    request.assert_not_awaited()


@pytest.mark.parametrize('changes', [dict(id=''), dict(id=100), dict(ts='1.2'), dict(ts=True),
    dict(amount='NaN'), dict(amount='Infinity'), dict(amount=1.2), dict(amount=''),
    dict(amount='-1'), dict(type=TYPES[1]), dict(coin='BTC'), dict(symbol='BTCUSD'),
    dict(category='SPOT'), dict(positionType='unexpected'), dict(posSide='unexpected')])
def test_rejects_malformed_or_conflicting_funding_schema(changes):
    request = AsyncMock(return_value=page([row(**changes)]))
    with pytest.raises(ValueError):
        run(request)


@pytest.mark.parametrize('payload', [None, {}, dict(code='40085', msg='secret synthetic-key'),
    dict(code='00000', data={}), dict(code='00000', data=dict(list=[], cursor=12)),
    dict(code='00000', data=dict(list=[row()]*101, cursor=''))])
def test_api_errors_and_incomplete_schema_raise_sanitized(payload):
    with pytest.raises(ValueError) as caught:
        run(AsyncMock(return_value=payload))
    assert 'synthetic-key' not in str(caught.value)


def test_repeated_cursor_and_nonprogress_pages_raise():
    for pages in ([page([row()], 'a'), page([row(id='2')], 'a')],
                  [page([row()], 'a'), page([row()], 'b')]):
        with pytest.raises(ValueError):
            run(AsyncMock(side_effect=pages))


def test_overlapping_identical_rows_dedupe_when_page_also_progresses():
    request = AsyncMock(side_effect=[page([row()], 'a'), page([row(), row(id='2')])] + [page()]*7)
    assert run(request) == {'BTC': Decimal('2.50')}


def test_conflicting_same_account_id_fails_closed():
    request = AsyncMock(side_effect=[page([row()], 'a'), page([row(amount='2')])])
    with pytest.raises(ValueError):
        run(request)


def test_later_page_or_profile_failure_returns_no_partial_result():
    for pages in ([page([row()], 'a'), RuntimeError('transport')],
                  [page([row()])]+[page()]*7+[RuntimeError('transport')]):
        with pytest.raises((ValueError, RuntimeError)):
            run(AsyncMock(side_effect=pages), [CREDS, dict(CREDS, api_key='second')])


def test_request_cap_is_global_across_types_products_and_profiles():
    request = AsyncMock(return_value=page())
    with pytest.raises(ValueError, match='budget'):
        run(request, max_pages=7)
    assert request.await_count == 7


@pytest.mark.parametrize('kwargs', [dict(api_base='http://api.bitget.com'),
    dict(api_base='https://api.bitget.com/evil'), dict(account_mode='classic'),
    dict(max_pages=0), dict(max_pages=257), dict(max_pages=True),
    dict(start=START.replace(tzinfo=None)), dict(start=START+timedelta(microseconds=1)),
    dict(start=NOW-timedelta(days=91)), dict(end=NOW+timedelta(seconds=1))])
def test_preflight_validation_before_network(kwargs):
    module = importlib.import_module('src.services.bitget_funding')
    args = dict(store=SimpleNamespace(get_exchange_credentials=lambda _: CREDS), refs=['bitget:p'],
                start=START, end=END, request_json=AsyncMock(), api_base=BASE, now=NOW)
    args.update(kwargs)
    with pytest.raises(ValueError):
        asyncio.run(module.fetch_bitget_funding(**args))
    args['request_json'].assert_not_awaited()


@pytest.mark.parametrize('credentials', [{}, dict(CREDS, api_key=''), dict(CREDS, api_secret=None),
                                         dict(CREDS, api_passphrase=' ')])
def test_no_environment_or_default_credential_fallback(credentials, monkeypatch):
    monkeypatch.setenv('BITGET_API_KEY', 'must-not-use')
    request = AsyncMock(return_value=page())
    with pytest.raises(ValueError, match='credentials'):
        run(request, [credentials])
    request.assert_not_awaited()


def test_transport_errors_are_sanitized_without_credentials_or_url():
    with pytest.raises(ValueError) as caught:
        run(AsyncMock(side_effect=RuntimeError('synthetic-key secret URL query')))
    assert 'synthetic-key' not in str(caught.value)
    assert caught.value.__suppress_context__


def test_more_than_ten_thousand_events_and_global_pagination_budget():
    pages = [page([row(id=str(i*100+j)) for j in range(100)], str(i)) for i in range(101)]
    request = AsyncMock(side_effect=pages + [page()] * 8)
    assert run(request) == {'BTC': Decimal('12625.00')}
    assert request.await_count == 109


def test_full_page_without_cursor_does_not_silently_truncate():
    with pytest.raises(ValueError, match='cursor'):
        run(AsyncMock(return_value=page([row(id=str(i)) for i in range(100)])))


def test_pacing_across_all_requests_and_signing_after_wait(monkeypatch):
    module = importlib.import_module('src.services.bitget_funding')
    events = []
    original = module.BitgetRealConnector._build_signed_headers
    def signed(self, *args, **kwargs):
        events.append('sign')
        return original(self, *args, **kwargs)
    async def sleep(seconds):
        assert seconds == 0.51
        events.append('sleep')
    monkeypatch.setattr(module.BitgetRealConnector, '_build_signed_headers', signed)
    run(AsyncMock(return_value=page()), sleep=sleep)
    assert events == ['sign'] + ['sleep', 'sign']*7
