from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qsl

import httpx
import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

START = datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc)
END = START + timedelta(days=1)
START_MS = int(START.timestamp() * 1000)
END_MS = int(END.timestamp() * 1000)
# Public synthetic test keys, never production credentials.
KEY = '0x' + '11' * 32
KEY2 = '0x' + '22' * 32
USER = '0x' + 'ab' * 20
USER2 = '0x' + 'cd' * 20
BASE = 'https://fapi.asterdex.com'


def credentials(user=USER, key=KEY):
    return {'user_address': user, 'signer': Account.from_key(key).address,
            'signer_private_key': key}


def row(record_id='one', **changes):
    result = dict(tranId=record_id, symbol='BTCUSDT', asset='USDT',
                  incomeType='FUNDING_FEE', income='0.123456789123456789', time=START_MS)
    result.update(changes)
    return result


def run(request, profiles=None, start=START, end=END, base=BASE):
    from src.services.aster_funding import fetch_aster_funding
    profiles = profiles if profiles is not None else {'aster:a': credentials()}
    store = SimpleNamespace(get_exchange_credentials=lambda ref: profiles[ref])
    return asyncio.run(fetch_aster_funding(store, list(profiles), start, end, request, base))


def test_exact_signed_wire_and_half_open_signed_decimal_totals(monkeypatch):
    import src.connectors.aster_connector as connector
    monkeypatch.setattr(connector, 'get_settings', lambda: pytest.fail('settings read'))
    monkeypatch.setattr(connector, '_runtime_credentials', lambda *_: pytest.fail('runtime read'))
    request = AsyncMock(return_value=[row(), row('neg', income='-0.1'),
        row('usdc', symbol='BTCUSDC', asset='USDC', income='2'),
        row('opaque', symbol='NCCOXAG2USDUSDT', income='-1'),
        row('before', time=START_MS - 1), row('end', time=END_MS)])
    assert run(request) == {'BTC': Decimal('2.023456789123456789'), 'NCCOXAG2USD': Decimal('-1')}
    args, kwargs = request.call_args
    assert args == ('GET', BASE + '/fapi/v3/income')
    assert kwargs['follow_redirects'] is False
    assert not kwargs.get('headers')
    wire = httpx.Request(*args, params=kwargs['params']).url.query.decode()
    message, signature = wire.rsplit('&signature=', 1)
    assert message == '&'.join(sorted(message.split('&')))
    params = dict(parse_qsl(message))
    assert params['startTime'] == str(START_MS)
    assert params['endTime'] == str(END_MS - 1)
    assert params['incomeType'] == 'FUNDING_FEE' and params['limit'] == '1000'
    assert set(params) == {'startTime', 'endTime', 'incomeType', 'limit', 'nonce', 'user', 'signer'}
    typed = encode_typed_data(domain_data={
        'name': 'AsterSignTransaction', 'version': '1', 'chainId': 1666,
        'verifyingContract': '0x0000000000000000000000000000000000000000'},
        message_types={'Message': [{'name': 'msg', 'type': 'string'}]},
        message_data={'msg': message})
    assert Account.recover_message(typed, signature=signature).lower() == credentials()['signer'].lower()


@pytest.mark.parametrize('bad', [
    {'income': 'NaN'}, {'income': 'Infinity'}, {'income': '-Infinity'}, {'income': ''},
    {'income': 'garbage'}, {'income': True}, {'income': 1}, {'income': 0.1},
    {'income': None}, {'asset': 'BTC'}, {'asset': 'usdt'}, {'incomeType': 'REALIZED_PNL'},
    {'time': True}, {'time': str(START_MS)}, {'time': 0}, {'time': None},
    {'tranId': ''}, {'tranId': 123}, {'tranId': None}, {'symbol': ''},
    {'symbol': 'BTC-USDT'}, {'symbol': 'BTCUSDC'}, {'symbol': 'USDT'}, {'symbol': None},
])
def test_invalid_record_fails_closed(bad):
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=[row(), row('bad', **bad)]))


@pytest.mark.parametrize('payload', [{}, {'code': 0, 'data': []},
    {'code': -1, 'msg': 'sensitive remote details'}, None, '', [None], [row()] * 1001])
def test_invalid_payload_is_sanitized(payload):
    with pytest.raises(ValueError) as error:
        run(AsyncMock(return_value=payload))
    assert 'sensitive' not in str(error.value)


@pytest.mark.parametrize('field,value', [
    ('user_address', ''), ('user_address', '0x123'), ('user_address', None),
    ('signer', '0x' + 'ff' * 20), ('signer', None),
    ('signer_private_key', ''), ('signer_private_key', 'invalid-secret'),
])
def test_invalid_credentials_never_request(field, value):
    creds = credentials()
    creds[field] = value
    request = AsyncMock()
    with pytest.raises(ValueError) as error:
        run(request, {'aster:a': creds})
    request.assert_not_called()
    assert 'invalid-secret' not in str(error.value)


def test_account_dedupe_is_user_scoped_not_signer_or_transaction_id(monkeypatch):
    import src.services.aster_funding as funding
    monkeypatch.setattr(funding, 'sleep', AsyncMock())
    request = AsyncMock(return_value=[row(), row()])
    profiles = {'a': credentials(), 'b': credentials(USER.upper().replace('0X', '0x'), KEY2),
                'c': credentials(USER2)}
    assert run(request, profiles) == {'BTC': Decimal('0.246913578246913578')}
    assert request.await_count == 2


def test_duplicate_account_still_requires_valid_credentials():
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=[]), {'a': credentials(), 'b': {'user_address': USER}})


def test_conflicting_transaction_fails_closed():
    with pytest.raises(ValueError, match='conflicting'):
        run(AsyncMock(return_value=[row(), row(income='9')]))


def test_empty_history_is_complete():
    assert run(AsyncMock(return_value=[])) == {}


def test_decimal_aggregation_does_not_round_long_signed_ledger_amounts():
    amount = '12345678901234567890.12345678901234567890'
    request = AsyncMock(return_value=[row(income=amount), row('negative', income='-0.1')])
    assert run(request) == {'BTC': Decimal('12345678901234567890.02345678901234567890')}


def test_saturated_intervals_split_disjointly_without_parent_count(monkeypatch):
    import src.services.aster_funding as funding
    sleep = AsyncMock()
    monkeypatch.setattr(funding, 'sleep', sleep)
    middle = (START_MS + END_MS) // 2
    events = [row(str(i), time=START_MS if i < 800 else middle, income='0.01') for i in range(1200)]

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
def test_saturation_and_budget_fail_closed(monkeypatch, single_ms):
    import src.services.aster_funding as funding
    monkeypatch.setattr(funding, 'sleep', AsyncMock())
    monkeypatch.setattr(funding, 'MAX_REQUESTS', 2)
    request = AsyncMock(return_value=[row(str(i)) for i in range(1000)])
    with pytest.raises(ValueError, match='saturated|budget'):
        run(request, end=START + timedelta(milliseconds=1) if single_ms else END)
    assert request.await_count <= 2


def test_later_page_or_profile_failure_returns_no_partial_totals(monkeypatch):
    import src.services.aster_funding as funding
    monkeypatch.setattr(funding, 'sleep', AsyncMock())
    with pytest.raises(ValueError, match='request failed') as error:
        run(AsyncMock(side_effect=[[row()], RuntimeError('secret URL')]),
            {'a': credentials(), 'b': credentials(USER2)})
    assert 'secret' not in str(error.value)


@pytest.mark.parametrize('base', ['http://fapi.asterdex.com', 'https://evil.invalid',
    'https://fapi.asterdex.com.evil.invalid', 'https://fapi.asterdex.com/path',
    'https://fapi.asterdex.com@evil.invalid', 'https://fapi.asterdex.com?x=1'])
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


def test_nonce_shared_with_original_connector_and_generated_after_pacing(monkeypatch):
    import src.connectors.aster_connector as connector
    import src.services.aster_funding as funding
    # Freeze only the connector clock, not asyncio's global monotonic clock.
    fixed_time = connector.time.time()
    monkeypatch.setattr(connector, 'time', SimpleNamespace(time=lambda: fixed_time))
    before = connector.AsterRealConnector._nonce()
    state = {'after_sleep': before}

    async def paced(_delay):
        state['after_sleep'] = connector.AsterRealConnector._nonce()

    monkeypatch.setattr(funding, 'sleep', paced)
    request = AsyncMock(return_value=[])
    run(request, {'a': credentials(), 'b': credentials(USER2)})
    first, second = [int(c.kwargs['params']['nonce']) for c in request.call_args_list]
    after = connector.AsterRealConnector._nonce()
    assert before < first < state['after_sleep'] < second < after
