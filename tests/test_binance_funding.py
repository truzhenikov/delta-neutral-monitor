from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qsl

import httpx
import pytest

START = datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc)
END = START + timedelta(days=1)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
BASE = 'https://fapi.binance.com'


def credentials(key='synthetic-key', secret='synthetic-secret'):
    return {'api_key': key, 'api_secret': secret}


def row(record_id=1, **changes):
    result = dict(tranId=record_id, symbol='BTCUSDT', asset='USDT',
                  incomeType='FUNDING_FEE', income='0.123456789123456789', time=START_MS)
    result.update(changes)
    return result


def run(request, profiles=None, start=START, end=END, base=BASE):
    from src.services.binance_funding import fetch_binance_funding
    profiles = profiles if profiles is not None else {'binance:a': credentials()}
    store = SimpleNamespace(get_exchange_credentials=lambda ref: profiles[ref])
    return asyncio.run(fetch_binance_funding(store, list(profiles), start, end, request, base))


def test_signed_exact_wire_and_actual_half_open_totals_without_environment(monkeypatch):
    import src.connectors.real_connectors as connector
    monkeypatch.setattr(connector, 'get_settings', lambda: pytest.fail('settings read'))
    monkeypatch.setattr(connector, '_runtime_credentials', lambda *_: pytest.fail('runtime read'))
    request = AsyncMock(return_value=[row(), row(2, income='-0.1'),
        row(3, symbol='BTCUSDC', asset='USDC', income='2'),
        row(4, symbol='NCCOXAG2USDUSDT', income='-1'),
        row(5, time=START_MS - 1), row(6, time=END_MS)])
    assert run(request) == {'BTC': Decimal('2.023456789123456789'), 'NCCOXAG2USD': Decimal('-1')}
    args, kwargs = request.call_args
    assert args == ('GET', BASE + '/fapi/v1/income')
    assert kwargs['follow_redirects'] is False
    assert kwargs['headers'] == {'X-MBX-APIKEY': credentials()['api_key']}
    wire = httpx.Request(*args, params=kwargs['params']).url.query.decode()
    query, signature = wire.rsplit('&signature=', 1)
    assert signature == hmac.new(credentials()['api_secret'].encode(), query.encode(), hashlib.sha256).hexdigest()
    params = dict(parse_qsl(query))
    assert params['startTime'] == str(START_MS)
    assert params['endTime'] == str(END_MS - 1)
    assert params['incomeType'] == 'FUNDING_FEE' and params['limit'] == '1000'
    assert params['recvWindow'] == '5000' and params['page'] == '1'
    assert set(params) == {'startTime', 'endTime', 'incomeType', 'limit', 'recvWindow', 'timestamp', 'page'}


@pytest.mark.parametrize('bad', [
    {'income': 'NaN'}, {'income': 'Infinity'}, {'income': '-Infinity'}, {'income': ''},
    {'income': 'garbage'}, {'income': True}, {'income': 1}, {'income': 0.1},
    {'income': None}, {'income': '1_0'}, {'income': ' 1'}, {'income': '１'},
    {'asset': 'BTC'}, {'asset': 'usdt'}, {'incomeType': 'REALIZED_PNL'},
    {'time': True}, {'time': str(START_MS)}, {'time': 0}, {'time': None},
    {'tranId': ''}, {'tranId': True}, {'tranId': -1}, {'tranId': None}, {'tranId': 1.0},
    {'symbol': ''}, {'symbol': 'BTC-USDT'}, {'symbol': 'BTCUSDC'}, {'symbol': 'USDT'}, {'symbol': None},
])
def test_invalid_record_fails_closed(bad):
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=[row(), row(2, **bad)]))


@pytest.mark.parametrize('payload', [{}, {'code': 200, 'data': []},
    {'code': -1, 'msg': 'sensitive details'}, None, '', [None], [row()] * 1001])
def test_invalid_payload_is_sanitized(payload):
    with pytest.raises(ValueError) as error:
        run(AsyncMock(return_value=payload))
    assert 'sensitive' not in str(error.value)


@pytest.mark.parametrize('creds', [None, {}, {'api_key': 'key'}, {'api_secret': 'secret'},
    credentials('', 'secret'), credentials('key', ''), credentials(True), credentials(secret=3),
    credentials(' key'), credentials('key\n'), credentials(secret=' secret')])
def test_invalid_credentials_never_request(creds):
    request = AsyncMock()
    with pytest.raises(ValueError):
        run(request, {'binance:a': creds})
    request.assert_not_called()


def test_alias_dedupe_and_transaction_identity_are_api_key_scoped(monkeypatch):
    import src.services.binance_funding as funding
    monkeypatch.setattr(funding, 'sleep', AsyncMock())
    request = AsyncMock(return_value=[row(), row()])
    assert run(request, {'a': credentials(), 'b': credentials(), 'c': credentials('other')}) == {
        'BTC': Decimal('0.246913578246913578')}
    assert request.await_count == 2


def test_conflicting_alias_secrets_fail_before_any_request():
    request = AsyncMock()
    with pytest.raises(ValueError, match='conflicting'):
        run(request, {'a': credentials(), 'b': credentials(secret='other')})
    request.assert_not_called()


def test_conflicting_duplicate_transaction_fails_closed():
    with pytest.raises(ValueError, match='conflicting'):
        run(AsyncMock(return_value=[row(), row(income='9')]))


def test_integer_and_decimal_string_transaction_ids_dedupe():
    assert run(AsyncMock(return_value=[row(), row('1')])) == {'BTC': Decimal('0.123456789123456789')}


def test_empty_history_is_complete():
    assert run(AsyncMock(return_value=[])) == {}


def test_decimal_totals_exact_under_low_precision():
    with localcontext() as context:
        context.prec = 3
        assert run(AsyncMock(return_value=[row(income='12345678901234567890.12345678901234567890'),
            row(2, income='-0.1')])) == {'BTC': Decimal('12345678901234567890.02345678901234567890')}


def test_saturated_intervals_split_without_parent_count_or_order_assumption(monkeypatch):
    import src.services.binance_funding as funding
    sleep = AsyncMock()
    monkeypatch.setattr(funding, 'sleep', sleep)
    middle = (START_MS + END_MS) // 2
    events = [row(i + 1, time=START_MS if i < 800 else middle, income='0.01') for i in range(1200)]

    async def ledger(*args, **kwargs):
        params = kwargs['params']
        return [r for r in reversed(events) if int(params['startTime']) <= r['time'] <= int(params['endTime'])][:1000]

    request = AsyncMock(side_effect=ledger)
    assert run(request) == {'BTC': Decimal('12.00')}
    assert [(int(c.kwargs['params']['startTime']), int(c.kwargs['params']['endTime']))
            for c in request.call_args_list] == [
        (START_MS, END_MS - 1), (START_MS, middle - 1), (middle, END_MS - 1)]
    assert sleep.await_count == 2
    assert all(c.args[0] >= 0.51 for c in sleep.call_args_list)


@pytest.mark.parametrize('single_ms', [True, False])
def test_saturation_and_request_budget_fail_closed(monkeypatch, single_ms):
    import src.services.binance_funding as funding
    monkeypatch.setattr(funding, 'sleep', AsyncMock())
    monkeypatch.setattr(funding, 'MAX_REQUESTS', 2)
    request = AsyncMock(return_value=[row(i + 1) for i in range(1000)])
    with pytest.raises(ValueError, match='saturated|budget'):
        run(request, end=START + timedelta(milliseconds=1) if single_ms else END)
    assert request.await_count <= 2


@pytest.mark.parametrize('split', [False, True])
def test_later_profile_or_interval_failure_never_returns_partial_totals(monkeypatch, split):
    import src.services.binance_funding as funding
    monkeypatch.setattr(funding, 'sleep', AsyncMock())
    replies = [[row()], RuntimeError('secret URL')]
    profiles = {'a': credentials(), 'b': credentials('other')}
    if split:
        replies.insert(0, [row(i + 1) for i in range(1000)])
        profiles.pop('b')
    with pytest.raises(ValueError, match='request failed') as error:
        run(AsyncMock(side_effect=replies), profiles)
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize('base', ['http://fapi.binance.com', 'https://evil.invalid',
    'https://fapi.binance.com.evil.invalid', 'https://fapi.binance.com/path',
    'https://user@fapi.binance.com', 'https://fapi.binance.com?x=1',
    'https://fapi.binance.com:443', BASE + '//'])
def test_untrusted_origin_never_receives_credentials(base):
    request = AsyncMock()
    with pytest.raises(ValueError, match='untrusted'):
        run(request, base=base)
    request.assert_not_called()


@pytest.mark.parametrize('start,end', [(START.replace(tzinfo=None), END), (START, START),
    (END, START), (START + timedelta(microseconds=1), END)])
def test_invalid_or_submillisecond_window_rejected(start, end):
    request = AsyncMock()
    with pytest.raises(ValueError, match='window'):
        run(request, start=start, end=end)
    request.assert_not_called()


def test_signing_timestamp_generated_after_pacing(monkeypatch):
    import src.services.binance_funding as funding
    state = {'now': START}
    monkeypatch.setattr(funding, 'datetime', SimpleNamespace(now=lambda _tz: state['now']))

    async def paced(_delay):
        state['now'] = END

    monkeypatch.setattr(funding, 'sleep', paced)
    request = AsyncMock(return_value=[])
    run(request, {'a': credentials(), 'b': credentials('other')})
    assert [c.kwargs['params']['timestamp'] for c in request.call_args_list] == [str(START_MS), str(END_MS)]
