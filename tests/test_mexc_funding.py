import asyncio
import hashlib
import hmac
import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

BASE = 'https://contract.mexc.com'
START = datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc)
END = START + timedelta(days=1)
LOW, HIGH = 1791966600000, 1792053000000
CREDS = dict(api_key='test-key', api_secret='test-secret')


def row(i=1, **changes):
    result = dict(id=i, symbol='BTC_USDT', funding=Decimal('1.25'), settleTime=LOW)
    result.update(changes)
    return result


def payload(rows=(), page=1, count=None):
    count = len(rows) if count is None else count
    return dict(success=True, code=0, data=dict(pageSize=100, currentPage=page,
        totalCount=count, totalPage=(count + 99) // 100, resultList=list(rows)))


def run(request, profiles=None, start=START, end=END, base=BASE):
    from src.services.mexc_funding import fetch_mexc_funding
    profiles = {'mexc:a': CREDS} if profiles is None else profiles
    store = SimpleNamespace(get_exchange_credentials=lambda ref: profiles[ref])
    return asyncio.run(fetch_mexc_funding(store, list(profiles), start, end, request, base))


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    if importlib.util.find_spec('src.services.mexc_funding'):
        import src.services.mexc_funding as m
        monkeypatch.setattr(m, 'sleep', AsyncMock())


def test_module_available():
    assert importlib.util.find_spec('src.services.mexc_funding') is not None


def test_signed_wire_boundaries_and_sign():
    request = AsyncMock(return_value=payload([row(), row(2, funding='-0.5'),
        row(3, settleTime=HIGH), row(4, settleTime=LOW-1),
        row(5, settleTime=Decimal(f'{HIGH-1}.999999999'), funding='0.000000000000000001')]))
    assert run(request) == {'BTC': Decimal('0.750000000000000001')}
    args, kw = request.call_args
    assert args == ('GET', BASE + '/api/v1/private/position/funding_records')
    assert kw['follow_redirects'] is False
    assert kw['params'] == dict(end_time=HIGH, page_num=1, page_size=100, start_time=LOW)
    wire = httpx.Request(*args, params=kw['params']).url.query.decode()
    assert list(kw['params']) == sorted(kw['params'])
    h = kw['headers']
    expected = hmac.new(b'test-secret', ('test-key'+h['Request-Time']+wire).encode(), hashlib.sha256).hexdigest()
    assert h['Signature'] == expected
    assert h['ApiKey'] == 'test-key'
    assert h['Recv-Window'] == '10'  # Official contract API units: seconds, max 60.


def test_full_pages_tied_times_and_account_alias_scope():
    first = [row(i) for i in range(100)]
    request = AsyncMock(side_effect=[payload(first, count=101), payload([row(100)], page=2, count=101), payload([row(0)])])
    profiles = {'mexc:a': CREDS, 'mexc:alias': CREDS, 'mexc:b': dict(api_key='b', api_secret='s')}
    assert run(request, profiles) == {'BTC': Decimal('127.50')}
    assert [c.kwargs['params']['page_num'] for c in request.call_args_list] == [1, 2, 1]
    assert request.call_args_list[-1].kwargs['headers']['ApiKey'] == 'b'


def test_exact_saturated_terminal_page():
    assert run(AsyncMock(return_value=payload([row(i) for i in range(100)]))) == {'BTC': Decimal('125')}


def test_complete_empty_is_no_rows():
    assert run(AsyncMock(return_value=payload())) == {}


def test_raw_json_decimal_transport_preserves_cutoff():
    from src.services.mexc_funding import fetch_mexc_funding
    raw = ('{"success":true,"code":0,"data":{"pageSize":100,"currentPage":1,'
           '"totalCount":1,"totalPage":1,"resultList":[{"id":1,"symbol":"BTC_USDT",'
           '"funding":0.000000000000000000123456789,"settleTime":1792052999999.999999999}]}}')
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, content=raw))) as client:
            async def request(method, url, **kwargs):
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json(parse_float=Decimal)
            store = SimpleNamespace(get_exchange_credentials=lambda _: CREDS)
            return await fetch_mexc_funding(store, ['mexc:a'], START, END, request, BASE)
    assert asyncio.run(scenario()) == {'BTC': Decimal('0.000000000000000000123456789')}


@pytest.mark.parametrize('bad', [None, '', 'bad', 'NaN', 'Infinity', '-Infinity', True, 1.25])
def test_bad_amount_fails(bad):
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=payload([row(funding=bad)])))


@pytest.mark.parametrize('field', ['funding', 'settleTime', 'id', 'symbol'])
def test_missing_required_fields(field):
    record = row()
    del record[field]
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=payload([record])))


@pytest.mark.parametrize('changes', [dict(settleTime=None), dict(settleTime=True), dict(settleTime='NaN'),
    dict(settleTime=1.5), dict(id=True), dict(id=''), dict(symbol='BTC_USD'),
    dict(symbol='BTC_USDC'), dict(symbol='btc_USDT'), dict(currency='BTC'), dict(asset='USDC')])
def test_bad_record_fails(changes):
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=payload([row(**changes)])))


def test_huge_exact_totals_under_small_context():
    amount = '1234567890123456789012345678901234567890.000000000001'
    with localcontext() as ctx:
        ctx.prec = 4
        assert run(AsyncMock(return_value=payload([row(funding=amount), row(2, funding='0.000000000009')]))) == {
            'BTC': Decimal('1234567890123456789012345678901234567890.000000000010')}


def test_microsecond_boundaries_preserve_source_precision():
    request = AsyncMock(return_value=payload([row(settleTime=Decimal(f'{LOW}.001')), row(2, settleTime=LOW)]))
    assert run(request, start=START+timedelta(microseconds=1)) == {'BTC': Decimal('1.25')}
    assert request.call_args.kwargs['params']['start_time'] == LOW


@pytest.mark.parametrize('later', [RuntimeError('sensitive upstream'), payload([row(0)], page=2, count=101),
    payload([], page=2, count=101), dict(success=False, code=1002, message='secret')])
def test_later_page_failure_not_partial(later):
    with pytest.raises(ValueError):
        run(AsyncMock(side_effect=[payload([row(i) for i in range(100)], count=101), later]))


def test_later_profile_failure_not_partial():
    with pytest.raises(ValueError, match='request failed'):
        run(AsyncMock(side_effect=[payload([row()]), RuntimeError('secret')]),
            {'mexc:a': CREDS, 'mexc:b': dict(api_key='b', api_secret='s')})


@pytest.mark.parametrize('change', [dict(currentPage=2), dict(totalCount=2), dict(totalPage=2),
    dict(pageSize=20), dict(totalCount=True), dict(resultList=None)])
def test_inconsistent_pagination_fails(change):
    data = payload([row()])
    data['data'].update(change)
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=data))


def test_budget_exhaustion_after_partial(monkeypatch):
    import src.services.mexc_funding as m
    monkeypatch.setattr(m, 'MAX_REQUESTS', 1)
    with pytest.raises(ValueError, match='budget'):
        run(AsyncMock(return_value=payload([row(i) for i in range(100)], count=101)))


def test_wait_before_fresh_auth(monkeypatch):
    import src.services.mexc_funding as m
    clock=[1000000000]
    monkeypatch.setattr(m, 'time_ns', lambda: clock[0])
    async def wait(delay):
        assert delay >= .11
        clock[0] += 2000000000
    monkeypatch.setattr(m, 'sleep', wait)
    request=AsyncMock(side_effect=[payload([row(i) for i in range(100)],count=101),payload([row(100)],page=2,count=101)])
    run(request)
    assert [c.kwargs['headers']['Request-Time'] for c in request.call_args_list] == ['1000', '3000']


@pytest.mark.parametrize('base', ['https://evil.test', BASE+'/', BASE+'@evil.test', 'http://contract.mexc.com', 'https://api.mexc.com'])
def test_untrusted_origin_before_credentials(base):
    from src.services.mexc_funding import fetch_mexc_funding
    store=SimpleNamespace(get_exchange_credentials=lambda _: pytest.fail('credential read'))
    with pytest.raises(ValueError, match='untrusted'):
        asyncio.run(fetch_mexc_funding(store,['mexc:a'],START,END,AsyncMock(),base))


@pytest.mark.parametrize('profiles', [{'gate:a':CREDS}, {'mexc:a':{}}, {'mexc:a':dict(api_key='x',api_secret='')},
    {'mexc:a':CREDS,'mexc:b':dict(api_key='test-key',api_secret='different')}])
def test_invalid_profiles_no_network(profiles):
    request=AsyncMock()
    with pytest.raises(ValueError):
        run(request, profiles)
    request.assert_not_awaited()


@pytest.mark.parametrize('start,end', [(END,START), (START,START), (START.replace(tzinfo=None),END)])
def test_invalid_window_no_network(start,end):
    request=AsyncMock()
    with pytest.raises(ValueError):
        run(request, start=start, end=end)
    request.assert_not_awaited()
