"""Read-only funding contract; all credentials and ledger rows are synthetic."""
import asyncio
import base64
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import pytest

NOW = datetime(2026, 10, 15, 9, tzinfo=timezone.utc)
START = NOW - timedelta(days=1)
END = NOW
BASE = 'https://www.okx.com'
CREDS = dict(api_key='test-key', api_secret='test-secret', api_passphrase='test-pass')


def row(bill_id='1000', **updates):
    data = dict(billId=bill_id, ts=str(int(START.timestamp() * 1000)), type='8',
                subType='174', instType='SWAP', instId='BTC-USDT-SWAP', ccy='USDT',
                pnl='1.25', balChg='1.25', posBalChg='0')
    data.update(updates)
    return data


def page(rows):
    return dict(code='0', msg='', data=rows)


def run(request, refs=None, credentials=None, **kwargs):
    from src.services.okx_funding import fetch_okx_funding
    creds = credentials or {'okx:p1': CREDS}
    store = SimpleNamespace(get_exchange_credentials=lambda ref: creds[ref])
    return asyncio.run(fetch_okx_funding(
        store, refs or ['okx:p1'], kwargs.pop('start', START), kwargs.pop('end', END),
        request, kwargs.pop('api_base', BASE), now=kwargs.pop('now', NOW),
        sleep=kwargs.pop('sleep', AsyncMock()), **kwargs))


def test_signed_pnl_includes_isolated_not_balance_change_and_exact_boundaries():
    request = AsyncMock(return_value=page([
        row('1004'), row('1003', pnl='-0.50', subType='173', balChg='0', posBalChg='-0.50'),
        row('1002', instId='ETH-USDC-SWAP', ccy='USDC', pnl='2.00'),
        row('1001', ts=str(int((START-timedelta(milliseconds=1)).timestamp()*1000)), pnl='99'),
        row('1000', ts=str(int(END.timestamp()*1000)), pnl='99')]))
    assert run(request) == {'BTC': Decimal('0.75'), 'ETH': Decimal('2.00')}


def test_actual_url_query_is_signed_and_read_only(monkeypatch):
    from src.connectors.real_connectors import OkxRealConnector
    stamp = '2026-10-15T09:00:00.123Z'
    monkeypatch.setattr(OkxRealConnector, '_okx_timestamp', lambda self: stamp)
    request = AsyncMock(return_value=page([]))
    assert run(request) == {}
    call = request.call_args
    method, url = call.args
    assert method == 'GET'
    parts = urlsplit(url)
    assert parts.scheme + '://' + parts.netloc == BASE
    assert parts.path == '/api/v5/account/bills'
    assert parse_qs(parts.query) == dict(instType=['SWAP'], type=['8'],
        begin=[str(int(START.timestamp()*1000))], end=[str(int(END.timestamp()*1000)-1)], limit=['100'])
    expected = base64.b64encode(hmac.new(b'test-secret',
        (stamp+'GET'+parts.path+'?'+parts.query).encode(), hashlib.sha256).digest()).decode()
    assert call.kwargs['headers']['OK-ACCESS-SIGN'] == expected
    assert call.kwargs['headers']['OK-ACCESS-KEY'] == 'test-key'
    assert call.kwargs['headers']['OK-ACCESS-PASSPHRASE'] == 'test-pass'
    assert call.kwargs['headers']['OK-ACCESS-TIMESTAMP'] == stamp
    assert 'params' not in call.kwargs  # no later query re-encoding
    assert call.kwargs['follow_redirects'] is False


def test_full_pages_use_minimum_bill_id_cursor_and_rate_limit():
    request = AsyncMock(side_effect=[page([row(str(n)) for n in range(1100,1000,-1)]),
                                     page([row('1000', pnl='-0.25', subType='173')])])
    sleep = AsyncMock()
    assert run(request, sleep=sleep) == {'BTC': Decimal('124.75')}
    assert parse_qs(urlsplit(request.call_args_list[1].args[1]).query)['after'] == ['1001']
    sleep.assert_awaited_once_with(0.2)


@pytest.mark.parametrize('bad', [
    {'pnl': None}, {'pnl': ''}, {'pnl': 'NaN'}, {'pnl': 'Infinity'}, {'pnl': 'oops'},
    {'pnl': '-1', 'subType': '174'}, {'pnl': '1', 'subType': '173'},
    {'ccy': 'BTC'}, {'instId': 'BTC-USD-SWAP'}, {'instId': 'BTC-USDC-SWAP'},
    {'instId': '-USDT-SWAP'}, {'instId': 'BTC-USDT-SWAP-extra'},
    {'type': '2'}, {'subType': '1'}, {'instType': 'FUTURES'}, {'billId': ''},
    {'billId': '-1'}, {'billId': '1.0'}, {'ts': 'broken'}, {'ts': None},
])
def test_bad_records_fail_closed(bad):
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=page([row(**bad)])))


@pytest.mark.parametrize('payload', [[], {}, {'code':'500','msg':'sensitive','data':[]},
    {'code':0,'data':[]}, {'code':'0','data':{}}, page([None]), page([row()]*101)])
def test_malformed_or_business_error_fails_closed(payload):
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=payload))


@pytest.mark.parametrize('tail', [[row('1100')], [row('1001')], [row('1000'),row('1000')],
                                  [row('1000',pnl='2'),row('1001')]])
def test_stuck_overlap_duplicate_or_conflicting_paging_rejected(tail):
    request = AsyncMock(side_effect=[page([row(str(n)) for n in range(1100,1000,-1)]),page(tail)])
    with pytest.raises(ValueError):
        run(request)


def test_non_descending_ids_fail_closed_to_avoid_skipped_records():
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=page([row('100'),row('200')])))


def test_page_cap_fails_instead_of_partial_success():
    with pytest.raises(ValueError):
        run(AsyncMock(return_value=page([row(str(n)) for n in range(1100,1000,-1)])),max_pages=1)


def test_same_api_key_alias_is_fetched_once():
    request = AsyncMock(return_value=page([row()]))
    assert run(request,refs=['okx:p1','okx:alias'],
               credentials={'okx:p1':CREDS,'okx:alias':dict(CREDS)}) == {'BTC':Decimal('1.25')}
    assert request.await_count == 1


def test_global_bill_id_dedup_and_conflict_detection():
    creds = {'okx:p1':CREDS,'okx:p2':dict(CREDS,api_key='another-key')}
    assert run(AsyncMock(side_effect=[page([row()]),page([row()])]),
               refs=list(creds), credentials=creds) == {'BTC':Decimal('1.25')}
    with pytest.raises(ValueError):
        run(AsyncMock(side_effect=[page([row()]),page([row(pnl='2')])]),refs=list(creds),credentials=creds)


def test_second_profile_failure_returns_no_partial_totals():
    creds = {'okx:p1':CREDS,'okx:p2':dict(CREDS,api_key='another-key')}
    with pytest.raises(RuntimeError):
        run(AsyncMock(side_effect=[page([row()]),RuntimeError('upstream')]),refs=list(creds),credentials=creds)


@pytest.mark.parametrize('key', list(CREDS))
def test_missing_credential_does_not_request(key):
    request = AsyncMock()
    with pytest.raises(ValueError):
        run(request,credentials={'okx:p1':dict(CREDS,**{key:''})})
    request.assert_not_called()


@pytest.mark.parametrize('base',[ 'http://www.okx.com','https://evil.test','https://www.okx.com.evil',
    'https://www.okx.com/path','https://user@www.okx.com','https://www.okx.com:443'])
def test_origin_validated_before_credentials_or_requests(base):
    from src.services.okx_funding import fetch_okx_funding
    store = SimpleNamespace(get_exchange_credentials=lambda _: pytest.fail('credential access'))
    request = AsyncMock()
    with pytest.raises(ValueError):
        asyncio.run(fetch_okx_funding(store,['okx:p1'],START,END,request,base,now=NOW))
    request.assert_not_called()


@pytest.mark.parametrize('changes',[{'start':NOW-timedelta(days=8)}, {'end':NOW+timedelta(seconds=1)},
    {'end':START}, {'start':START.replace(tzinfo=None)}, {'end':END.replace(tzinfo=None)}])
def test_uncovered_or_invalid_window_rejected_before_network(changes):
    request = AsyncMock()
    with pytest.raises(ValueError):
        run(request,**changes)
    request.assert_not_called()
