import asyncio
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

BASE = 'https://api.gateio.ws/api/v4'
START = datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc)
END = START + timedelta(days=1)
LOW = 1791966600
HIGH = LOW + 86400
CREDS = {'api_key': 'synthetic-key', 'api_secret': 'synthetic-secret'}


def row(record_id='one', **changes):
    result = dict(id=record_id, contract='BTC_USDT', type='fund', time=LOW, change='1.25')
    result.update(changes)
    return result


def run(request, profiles=None, start=START, end=END, base=BASE, settle='usdt'):
    from src.services.gate_funding import fetch_gate_funding
    profiles = profiles if profiles is not None else {'gate:a': CREDS}
    store = SimpleNamespace(get_exchange_credentials=lambda ref: profiles[ref])
    return asyncio.run(fetch_gate_funding(store, list(profiles), start, end, request, base, settle))


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    import importlib.util
    if importlib.util.find_spec('src.services.gate_funding') is not None:
        from src.services import gate_funding
        monkeypatch.setattr(gate_funding, 'sleep', AsyncMock())


def test_gate_ledger_available():
    import importlib.util
    assert importlib.util.find_spec('src.services.gate_funding') is not None


def test_signed_wire_and_exact_half_open_totals(monkeypatch):
    import src.connectors.real_connectors as connector
    monkeypatch.setattr(connector, 'get_settings', lambda: pytest.fail('settings read'))
    monkeypatch.setattr(connector, '_runtime_credentials', lambda *_: pytest.fail('runtime credentials read'))
    request = AsyncMock(return_value=[row(), row('negative', change='-0.2'),
        row('before', time=str(LOW - 1)), row('end', time=HIGH),
        row('fraction', time=f'{HIGH - 1}.999999999', change='0.000000000000000001'),
        row('opaque', contract='NCCOXAG2USD_USDT', change='-2')])
    assert run(request) == {'BTC': Decimal('1.050000000000000001'), 'NCCOXAG2USD': Decimal('-2')}
    args, kwargs = request.call_args
    assert args == ('GET', BASE + '/futures/usdt/account_book')
    assert kwargs['follow_redirects'] is False
    assert kwargs['params'] == {'type': 'fund', 'from': LOW, 'to': HIGH, 'limit': 100, 'offset': 0}
    wire = httpx.Request(*args, params=kwargs['params']).url.query.decode()
    headers = kwargs['headers']
    message = '\n'.join(['GET', '/api/v4/futures/usdt/account_book', wire,
                          hashlib.sha512(b'').hexdigest(), headers['Timestamp']])
    assert headers['KEY'] == CREDS['api_key']
    assert headers['SIGN'] == hmac.new(CREDS['api_secret'].encode(), message.encode(), hashlib.sha512).hexdigest()


def test_complete_offset_pages_including_empty_terminal():
    request = AsyncMock(side_effect=[[row(str(i)) for i in range(100)], []])
    assert run(request) == {'BTC': Decimal('125')}
    assert [c.kwargs['params']['offset'] for c in request.call_args_list] == [0, 100]


def test_short_terminal_page_and_profiles_alias_dedupe():
    request = AsyncMock(side_effect=[[row(str(i)) for i in range(100)], [row('last', change='-1')], []])
    profiles = {'gate:a': CREDS, 'gate:alias': CREDS,
                'gate:b': dict(api_key='another-key', api_secret='another-secret')}
    assert run(request, profiles) == {'BTC': Decimal('124')}
    assert [c.kwargs['params']['offset'] for c in request.call_args_list] == [0, 100, 0]


@pytest.mark.parametrize('second', [[row('0')], [row('0', change='99')]])
def test_overlap_or_conflicting_ids_fail(second):
    with pytest.raises(ValueError):
        run(AsyncMock(side_effect=[[row(str(i)) for i in range(100)], second]))


def test_duplicate_within_page_fails():
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=[row(), row()]))


def test_request_budget_exhaustion(monkeypatch):
    import src.services.gate_funding as funding
    monkeypatch.setattr(funding, 'MAX_REQUESTS', 2)
    request = AsyncMock(side_effect=[[row(str(i + page * 100)) for i in range(100)] for page in range(2)])
    with pytest.raises(ValueError, match='budget'):
        run(request)
    assert request.await_count == 2


@pytest.mark.parametrize('bad', [
    {'id': ''}, {'id': None}, {'id': 1}, {'contract': None}, {'contract': ''},
    {'contract': 'USDT'}, {'contract': 'BTCUSDT'}, {'contract': 'BTC_USDC'},
    {'contract': 'btc_USDT'}, {'type': 'fee'}, {'type': 'pnl'}, {'type': 'dnw'}, {'type': 'dividend'},
    {'change': 'NaN'}, {'change': 'Infinity'}, {'change': ''}, {'change': None},
    {'change': 1}, {'change': True}, {'change': 'bad'},
    {'time': True}, {'time': None}, {'time': ''}, {'time': 'NaN'}, {'time': 'Infinity'}, {'time': 0},
    {'currency': 'BTC'}, {'asset': 'USDC'},
])
def test_invalid_record_fails_closed(bad):
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=[row(), row('bad', **bad)]))


@pytest.mark.parametrize('payload', [{}, {'label': 'ERROR', 'message': 'sensitive'}, None, '', [None], [row()] * 101])
def test_invalid_payload_fails_sanitized(payload):
    with pytest.raises(ValueError) as exc:
        run(AsyncMock(return_value=payload))
    assert 'sensitive' not in str(exc.value)


@pytest.mark.parametrize('creds', [{}, {'api_key': 'key'}, {'api_secret': 'secret'}, None,
                                    {'api_key': 12, 'api_secret': 'secret'}])
def test_missing_credentials_fail_before_request(creds):
    request = AsyncMock()
    with pytest.raises(ValueError):
        run(request, {'gate:a': creds})
    request.assert_not_called()


@pytest.mark.parametrize('base,settle', [('http://api.gateio.ws/api/v4', 'usdt'),
    (BASE + '/evil', 'usdt'), ('https://api.gateio.ws.evil/api/v4', 'usdt'),
    ('https://user@api.gateio.ws/api/v4', 'usdt'), (BASE, 'btc'), (BASE, 'usdc')])
def test_reject_configuration_before_credentials(base, settle):
    from src.services.gate_funding import fetch_gate_funding
    store = SimpleNamespace(get_exchange_credentials=lambda _: pytest.fail('credentials read'))
    request = AsyncMock()
    with pytest.raises(ValueError):
        asyncio.run(fetch_gate_funding(store, ['gate:a'], START, END, request, base, settle))
    request.assert_not_called()


@pytest.mark.parametrize('start,end', [(START.replace(tzinfo=None), END), (START, START),
    (END, START), (START.replace(microsecond=1), END), (START, END.replace(microsecond=1))])
def test_invalid_window(start, end):
    with pytest.raises(ValueError):
        run(AsyncMock(), start=start, end=end)


def test_exact_addition_under_reduced_decimal_context():
    request = AsyncMock(return_value=[row(change='123456789012345678901234567890.123456789'),
                                      row('tiny', change='0.000000001')])
    with localcontext() as context:
        context.prec = 6
        assert run(request) == {'BTC': Decimal('123456789012345678901234567890.123456790')}


def test_pacing_across_profiles_and_signing_after_wait(monkeypatch):
    import src.services.gate_funding as funding
    import src.connectors.real_connectors as connector
    clock = {'time': 1.0}
    monkeypatch.setattr(connector, 'time', SimpleNamespace(time=lambda: clock['time']))
    async def wait(delay):
        assert delay >= 0.51
        clock['time'] += 1
    monkeypatch.setattr(funding, 'sleep', wait)
    request = AsyncMock(side_effect=[[row(str(i)) for i in range(100)], [], []])
    run(request, {'gate:a': CREDS, 'gate:b': dict(api_key='other', api_secret='secret')})
    assert [c.kwargs['headers']['Timestamp'] for c in request.call_args_list] == ['1.0', '2.0', '3.0']
