import asyncio
import base64
import importlib.util
from datetime import datetime, timedelta, timezone
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

BASE = 'https://api.orderly.org'
START = datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc)
END = START + timedelta(days=1)
LOW = 1791966600000
HIGH = LOW + 86400000
# A zero seed encodes as 32 leading base58 zeros; synthetic, never production.
CREDS = dict(account_id='synthetic-account', orderly_key='synthetic-public', orderly_secret='1' * 32)


def row(time=LOW, **changes):
    result = dict(symbol='PERP_BTC_USDC', funding_fee='-1.25', payment_type='Receive',
                  status='Accrued', created_time=time, updated_time=time, margin_mode='CROSS')
    result.update(changes)
    return result


def payload(rows, total=None, page=1, size=60):
    return dict(success=True, data=dict(rows=rows, meta=dict(total=len(rows) if total is None else total,
                current_page=page, records_per_page=size)))


def run(request, profiles=None, base=BASE, start=START, end=END):
    from src.services.woofi_funding import fetch_woofi_funding
    profiles = profiles if profiles is not None else {'woofi:a': CREDS}
    store = SimpleNamespace(get_exchange_credentials=lambda ref: profiles[ref])
    return asyncio.run(fetch_woofi_funding(store, list(profiles), start, end, request, base))


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    if importlib.util.find_spec('src.services.woofi_funding'):
        from src.services import woofi_funding
        monkeypatch.setattr(woofi_funding, 'sleep', AsyncMock())


def test_module_available():
    assert importlib.util.find_spec('src.services.woofi_funding') is not None


def test_signs_actual_wire_and_returns_received_signed_half_open_amounts():
    request = AsyncMock(return_value=payload([row(), row(LOW+1, funding_fee='0.2', payment_type='Pay', status='Settled'),
        row(LOW-1), row(HIGH), row(HIGH-1, symbol='PERP_NVDA_USDC', funding_fee='-0.00000001')]))
    assert run(request) == {'BTC': Decimal('1.05'), 'NVDA': Decimal('0.00000001')}
    args, kwargs = request.call_args
    assert args[0] == 'GET'
    assert kwargs['follow_redirects'] is False
    wire = httpx.Request(*args, **{k:v for k,v in kwargs.items() if k != 'follow_redirects'})
    assert wire.url.host == 'api.orderly.org'
    assert parse_qs(wire.url.query.decode()) == {k:[str(v)] for k,v in dict(start_t=LOW,end_t=HIGH,page=1,size=60).items()}
    h = kwargs['headers']
    signed = (h['orderly-timestamp']+'GET'+wire.url.raw_path.decode()).encode()
    Ed25519PrivateKey.from_private_bytes(bytes(32)).public_key().verify(
        base64.urlsafe_b64decode(h['orderly-signature']+'=='), signed)
    assert h['orderly-account-id'] == CREDS['account_id']


def test_full_pages_tied_timestamps_and_aliases():
    rows = [row(symbol=f'PERP_C{i}_USDC') for i in range(60)]
    profiles = {'woofi:a': CREDS, 'woofi:alias': dict(CREDS, orderly_key='other-key'),
                'woofi:b': dict(CREDS, account_id='other-account')}
    request = AsyncMock(side_effect=[payload(rows,total=61),payload([row(LOW+1)],total=61,page=2),payload([])])
    result = run(request, profiles)
    assert result['BTC'] == Decimal('1.25')
    assert len(result) == 61
    assert request.await_count == 3


@pytest.mark.parametrize('failure', [RuntimeError('sensitive URL'), payload([],total=61,page=2),
    payload([row()],total=61,page=2), payload([row(LOW+1)],total=62,page=2), {'success':False}])
def test_later_page_failure_returns_no_partial_result(failure):
    request = AsyncMock(side_effect=[payload([row(LOW+i) for i in range(60)],total=61),failure])
    with pytest.raises(ValueError) as exc:
        run(request)
    assert 'sensitive' not in str(exc.value)


def test_later_profile_failure_returns_no_partial_result():
    profiles = {'woofi:a': CREDS,'woofi:b':dict(CREDS,account_id='other')}
    with pytest.raises(ValueError):
        run(AsyncMock(side_effect=[payload([row()]),RuntimeError('secret')]),profiles)


@pytest.mark.parametrize('base',[BASE+'/',BASE+'/evil',BASE+'.evil', 'http://api.orderly.org',BASE+'?query=1'])
def test_untrusted_origin_precedes_credentials(base):
    from src.services.woofi_funding import fetch_woofi_funding
    store = SimpleNamespace(get_exchange_credentials=lambda _: pytest.fail('credential access'))
    with pytest.raises(ValueError):
        asyncio.run(fetch_woofi_funding(store,['woofi'],START,END,AsyncMock(),base))


@pytest.mark.parametrize('changes', [dict(funding_fee=None),dict(funding_fee='NaN'),dict(funding_fee='Infinity'),
    dict(funding_fee=1.25),dict(funding_fee='1',payment_type='Receive'),dict(payment_type='unknown'),
    dict(created_time=None),dict(created_time=LOW+0.5),dict(status='Unknown'),dict(margin_mode='unknown'),
    dict(symbol='PERP_BTC_USDT'),dict(currency='ETH'),dict(symbol=None),dict(funding_fee=True)])
def test_malformed_rows_fail(changes):
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=payload([row(**changes)])))


@pytest.mark.parametrize('bad',[{}, {'success':True}, payload([row(),row()]),
    payload([],total=1),payload([row()],page=2),payload([row()],size=100),payload([],total=True)])
def test_bad_metadata_or_overlap_fails(bad):
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=bad))


@pytest.mark.parametrize('missing',['account_id','orderly_key','orderly_secret'])
def test_missing_credentials(missing):
    with pytest.raises(ValueError):
        run(AsyncMock(),{'woofi:a':{k:v for k,v in CREDS.items() if k!=missing}})


def test_decimal_exact_under_low_precision():
    request = AsyncMock(return_value=payload([row(funding_fee='-123456789012345678901234567890.123456789'),
        row(LOW+1,funding_fee='-0.000000001')]))
    with localcontext() as ctx:
        ctx.prec=4
        assert run(request) == {'BTC':Decimal('123456789012345678901234567890.123456790')}


def test_raw_http_json_decimal_transport():
    raw = ('{"success":true,"data":{"meta":{"total":1,"current_page":1,"records_per_page":60},'
           '"rows":[{"symbol":"PERP_BTC_USDC","funding_fee":-0.12345678901234567890123456789,'
           '"payment_type":"Receive","status":"Settled","created_time":1792052999999,'
           '"margin_mode":"ISOLATED"}]}}')
    async def request(method, url, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda _: httpx.Response(200, content=raw))) as client:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json(parse_float=Decimal)
    assert run(request) == {'BTC': Decimal('0.12345678901234567890123456789')}


def test_full_final_page_and_ties_across_pages():
    first = [row(symbol=f'PERP_C{i}_USDC') for i in range(60)]
    request = AsyncMock(side_effect=[payload(first,total=61),payload([row()],total=61,page=2)])
    assert len(run(request)) == 61
    full = AsyncMock(return_value=payload(first))
    assert len(run(full)) == 60
    assert full.await_count == 1


def test_cross_and_isolated_same_symbol_timestamp_are_distinct():
    assert run(AsyncMock(return_value=payload([row(),row(margin_mode='ISOLATED')]))) == {'BTC':Decimal('2.50')}


def test_profile_signing_never_reloads_default(monkeypatch):
    from src.connectors import woofi_connector as connector
    monkeypatch.setattr(connector,'get_settings',lambda: pytest.fail('settings read'))
    monkeypatch.setattr(connector,'_runtime_credentials',lambda *_: pytest.fail('default profile read'))
    assert run(AsyncMock(return_value=payload([row()]))) == {'BTC':Decimal('1.25')}


def test_invalid_secret_sanitized():
    with pytest.raises(ValueError, match='missing or invalid WOOFi credentials'):
        run(AsyncMock(),{'woofi:a':dict(CREDS,orderly_secret='secret-not-base58')})


def test_empty_complete_ledger():
    assert run(AsyncMock(return_value=payload([]))) == {}


@pytest.mark.parametrize('start,end',[(START.replace(tzinfo=None),END),(START,START),
    (START.replace(microsecond=1),END)])
def test_invalid_window(start,end):
    with pytest.raises(ValueError):
        run(AsyncMock(),start=start,end=end)


def test_budget_and_pacing_fresh_signing(monkeypatch):
    from src.services import woofi_funding as f
    monkeypatch.setattr(f,'MAX_REQUESTS',1)
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=payload([row(LOW+i) for i in range(60)],total=61)))
    monkeypatch.setattr(f,'MAX_REQUESTS',256)
    sequence=[]
    async def wait(delay):
        assert delay >= 3
        sequence.append('wait')
    def clock():
        sequence.append('sign')
        return 1792100000000000000
    monkeypatch.setattr(f,'sleep',wait)
    monkeypatch.setattr(f,'time_ns',clock)
    run(AsyncMock(side_effect=[payload([row(LOW+i) for i in range(60)],total=61),payload([row(LOW+61)],total=61,page=2)]))
    assert sequence == ['sign','wait','sign']
