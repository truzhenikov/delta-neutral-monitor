from __future__ import annotations

import asyncio
import hashlib
import hmac
import importlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs

import httpx
import pytest

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import FundingReportService, FundingRow, MSK

NOW = datetime(2026, 10, 15, 12, tzinfo=MSK)
START, END = FundingReportService.completed_window(NOW)
BASE = 'https://api.ondoperps.xyz'


def row(**changes):
    result = dict(market='QQQ-USD.P', time=START.astimezone(timezone.utc).isoformat(),
                  amount='-0.125001', markPrice='100', positionSize='1',
                  positionDirection='long', rate='0.00125001', payer='long')
    result.update(changes)
    return result


def page(rows, cursor=''):
    return dict(success=True, result=rows, pageInfo=dict(prevCursor='', nextCursor=cursor))


def store(tmp_path, profiles=1):
    result = CredentialStore(tmp_path / 'credentials.json')
    for i in range(profiles):
        result.set_exchange_credentials(f'ondo:p{i}',
                                        dict(api_key=f'ondoKeyId_test{i}', api_secret='ondoApiSecret_test'))
    return result


def fetch(tmp_path, request, *, profiles=1, start=START, end=END, base=BASE):
    module = importlib.import_module('src.services.ondo_funding')
    credentials = store(tmp_path, profiles)
    return asyncio.run(module.fetch_ondo_funding(
        credentials, credentials.list_enabled_exchanges(), start, end, request, base))


def test_actual_signed_usdc_amounts_and_nanosecond_half_open_window(tmp_path):
    before = (START - timedelta(seconds=1)).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + '.999999999Z'
    last = (END - timedelta(seconds=1)).astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S') + '.999999999Z'
    request = AsyncMock(return_value=page([
        row(), row(time=before, amount='999'), row(time=last, amount='0.100000'),
        row(time=END.isoformat(), amount='999'),
        row(market='XAG-USD.P', amount='1.23', time='2026-10-15T08:00:00.015591459Z'),
    ]))
    assert fetch(tmp_path, request) == {'QQQ': Decimal('-0.025001'), 'XAG': Decimal('1.23')}
    call = request.call_args
    assert call.args[0] == 'GET'
    assert call.kwargs['follow_redirects'] is False
    wire = httpx.Request(call.args[0], call.args[1], headers=call.kwargs['headers']).url
    params = parse_qs(wire.query.decode())
    assert params == {'startTime': [str(int(START.timestamp() * 1000))],
                      'endTime': [str(int(END.timestamp() * 1000))], 'limit': ['1000']}
    headers = call.kwargs['headers']
    assert headers['ONDO-KEY-ID'] == 'ondoKeyId_test0'
    message = headers['ONDO-TIMESTAMP'] + 'GET' + wire.raw_path.decode()
    assert headers['ONDO-SIGN'] == hmac.new(b'ondoApiSecret_test', message.encode(), hashlib.sha256).hexdigest()


def test_pagination_opaque_cursor_overlap_and_fresh_signing_after_pacing(tmp_path, monkeypatch):
    module = importlib.import_module('src.services.ondo_funding')
    clock = [1000.0]
    async def wait(delay):
        assert delay >= 1.05
        clock[0] = round(clock[0] + delay, 2)
    sleep = AsyncMock(side_effect=wait)
    monkeypatch.setattr(module, 'sleep', sleep)
    monkeypatch.setattr(module, 'time', lambda: clock[0])
    request = AsyncMock(side_effect=[page([row()], 'opaque+/===='),
                                    page([row(), row(amount='0.2')], 'second'),
                                    page([row(amount='0.3')])])
    assert fetch(tmp_path, request) == {'QQQ': Decimal('0.374999')}
    calls = request.call_args_list
    assert len(calls) == 3 and sleep.await_count == 2
    assert parse_qs(httpx.URL(calls[1].args[1]).query.decode())['cursor'] == ['opaque+/====']
    assert 'opaque%2B%2F%3D%3D%3D%3D' in calls[1].args[1]
    for i, call in enumerate(calls):
        headers = call.kwargs['headers']
        assert headers['ONDO-TIMESTAMP'] == str(int((1000.0 + i * 1.05) * 1000))
        path = httpx.URL(call.args[1]).raw_path.decode()
        expected = hmac.new(b'ondoApiSecret_test',
                            (headers['ONDO-TIMESTAMP'] + 'GET' + path).encode(), hashlib.sha256).hexdigest()
        assert headers['ONDO-SIGN'] == expected


@pytest.mark.parametrize('payload', [[], {}, {'success': 1, 'result': [], 'pageInfo': {'nextCursor': ''}},
                                     {'success': False}, {'success': True, 'result': {}},
                                     {'success': True, 'result': [], 'pageInfo': None},
                                     {'success': True, 'result': [], 'pageInfo': {}},
                                     {'success': True, 'result': [], 'pageInfo': {'nextCursor': None}},
                                     {'success': True, 'result': [], 'pageInfo': {'nextCursor': 42}},
                                     page([None]), page([row()] * 1001)])
def test_malformed_payload_is_not_complete(tmp_path, payload):
    with pytest.raises(ValueError):
        fetch(tmp_path, AsyncMock(return_value=payload))


@pytest.mark.parametrize('changes', [dict(amount='NaN'), dict(amount='Infinity'), dict(amount=''),
                                    dict(amount=None), dict(amount=True), dict(amount='broken'),
                                    dict(market='QQQ-USDC'), dict(market='-USD.P'), dict(market=None),
                                    dict(time=None), dict(time='2026-10-15T08:00:00'),
                                    dict(time='2026-99-15T08:00:00Z'), dict(asset='USDT'), dict(currency='USD'),
                                    dict(time='2026-10-15T08:00:00.1234567891Z')])
def test_invalid_record_rejected_even_outside_window(tmp_path, changes):
    with pytest.raises(ValueError):
        fetch(tmp_path, AsyncMock(return_value=page([row(**changes)])))


@pytest.mark.parametrize('mode', ['cycle', 'repeat', 'empty', 'budget'])
def test_incomplete_paging_fails_closed(tmp_path, monkeypatch, mode):
    module = importlib.import_module('src.services.ondo_funding')
    monkeypatch.setattr(module, 'sleep', AsyncMock())
    monkeypatch.setattr(module, 'MAX_REQUESTS', 3)
    if mode == 'cycle':
        pages = [page([row(amount=str(i))], 'a' if i % 2 == 0 else 'b') for i in range(3)]
    elif mode == 'repeat':
        pages = [page([row()], 'a'), page([row()], 'b')]
    elif mode == 'empty':
        pages = [page([], 'a')]
    else:
        pages = [page([row(amount=str(i))], str(i)) for i in range(3)]
    request = AsyncMock(side_effect=pages)
    with pytest.raises(ValueError):
        fetch(tmp_path, request)
    assert request.await_count <= 3


def test_full_terminal_page_is_complete_by_explicit_empty_cursor(tmp_path):
    rows = [row(amount=str(i)) for i in range(1000)]
    request = AsyncMock(return_value=page(rows))
    assert fetch(tmp_path, request) == {'QQQ': sum((Decimal(str(i)) for i in range(1000)), Decimal(0))}
    assert request.await_count == 1


def test_empty_terminal_and_exact_duplicate_rows(tmp_path):
    assert fetch(tmp_path, AsyncMock(return_value=page([]))) == {}
    assert fetch(tmp_path, AsyncMock(return_value=page([row(), row()]))) == {'QQQ': Decimal('-0.125001')}


@pytest.mark.parametrize('base', ['http://api.ondoperps.xyz', BASE + '/evil', BASE + '@evil.invalid',
                                  'https://evil.invalid', BASE + '?query=1'])
def test_untrusted_origin_receives_no_credentials(tmp_path, base):
    request = AsyncMock()
    with pytest.raises(ValueError):
        fetch(tmp_path, request, base=base)
    request.assert_not_called()


@pytest.mark.parametrize('start,end', [(START.replace(tzinfo=None), END), (START, END.replace(tzinfo=None)),
                                      (END, START), (START, START)])
def test_invalid_windows_rejected_before_request(tmp_path, start, end):
    request = AsyncMock()
    with pytest.raises(ValueError):
        fetch(tmp_path, request, start=start, end=end)
    request.assert_not_called()


def test_profile_aggregation_alias_dedup_and_pacing(tmp_path, monkeypatch):
    module = importlib.import_module('src.services.ondo_funding')
    sleep = AsyncMock()
    monkeypatch.setattr(module, 'sleep', sleep)
    credentials = store(tmp_path, 2)
    credentials.set_exchange_credentials('ondo:alias', dict(api_key='ondoKeyId_test0', api_secret='ondoApiSecret_test'))
    request = AsyncMock(return_value=page([row()]))
    totals = asyncio.run(module.fetch_ondo_funding(credentials, credentials.list_enabled_exchanges(),
                                                  START, END, request, BASE))
    assert totals == {'QQQ': Decimal('-0.250002')}
    assert request.await_count == 2
    sleep.assert_awaited_once_with(1.05)


@pytest.mark.parametrize('missing', ['api_key', 'api_secret'])
def test_missing_credentials_fail_entire_venue(tmp_path, missing):
    credentials = store(tmp_path)
    credentials.get_exchange_credentials = lambda ref: {k: 'test' for k in ['api_key', 'api_secret'] if k != missing}
    request = AsyncMock()
    module = importlib.import_module('src.services.ondo_funding')
    with pytest.raises(ValueError):
        asyncio.run(module.fetch_ondo_funding(credentials, ['ondo:p0'], START, END, request, BASE))
    request.assert_not_called()


def test_builder_integrates_ondo_without_sending(tmp_path):
    credentials = store(tmp_path)
    request = AsyncMock(return_value=page([row()]))
    service = FundingReportService(credentials, tmp_path / 'state.json', request_json=request)
    report = asyncio.run(service.build_report(NOW))
    assert report.complete and report.rows == [FundingRow('Ondo', 'QQQ', Decimal('-0.125001'))]
    assert not (tmp_path / 'state.json').exists()


def test_late_profile_failure_preserves_other_venues_not_partial_ondo(tmp_path, monkeypatch):
    module = importlib.import_module('src.services.ondo_funding')
    monkeypatch.setattr(module, 'sleep', AsyncMock())
    credentials = store(tmp_path, 2)
    request = AsyncMock(side_effect=[page([row()]), RuntimeError('private secret')])
    service = FundingReportService(credentials, tmp_path / 'state.json', request_json=request)
    existing = FundingRow('Hyperliquid', 'BTC', Decimal('2'))
    service._hyperliquid_rows = AsyncMock(return_value=([existing], []))
    report = asyncio.run(service.build_report(NOW))
    assert not report.complete and report.rows == [existing]
    assert len(report.warnings) == 1 and 'Ondo' in report.warnings[0]
    assert 'private secret' not in report.warnings[0]


def test_dependency_wires_configured_ondo_origin(monkeypatch):
    import src.deps as deps
    settings = SimpleNamespace(funding_report_state_path='/tmp/not-written.json', hyperliquid_api_base='hl',
                               lighter_api_base='lighter', lighter_rh_api_base='lighter-rh', variational_api_base='variational',
                               bingx_api_base='bingx', okx_api_base='okx', ondo_api_base=BASE,
                               aster_api_base='https://fapi.asterdex.com',
                               gate_api_base='https://api.gateio.ws/api/v4',
                               woofi_api_base='https://api.orderly.org', mexc_funding_api_base='https://contract.mexc.com',
                               arcus_api_base='https://api.arcus.xyz',
                               binance_api_base='https://fapi.binance.com', bitget_api_base='https://api.bitget.com',
                               gate_settle_currency='usdt', request_timeout_sec=3)
    monkeypatch.setattr(deps, 'get_settings', lambda: settings)
    monkeypatch.setattr(deps, 'get_credential_store', lambda: None)
    deps.get_funding_report_service.cache_clear()
    try:
        assert deps.get_funding_report_service().ondo_api_base == BASE
    finally:
        deps.get_funding_report_service.cache_clear()
