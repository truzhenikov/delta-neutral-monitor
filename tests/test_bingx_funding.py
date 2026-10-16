from __future__ import annotations

import asyncio
import hashlib
import hmac
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import FundingReportService, MSK

NOW = datetime(2026, 10, 15, 12, tzinfo=MSK)
START, END = FundingReportService.completed_window(NOW)
START_MS, END_MS = int(START.timestamp() * 1000), int(END.timestamp() * 1000)


def row(record_id='one', **changes):
    result = dict(tranId=record_id, symbol='NCCOXAG2USD-USDT', incomeType='FUNDING_FEE',
                  income='0.64710945', asset='USDT', time=START_MS)
    result.update(changes)
    return result


def page(rows):
    return {'code': 0, 'data': rows}


def service(tmp_path, request, profiles=1):
    store = CredentialStore(tmp_path / 'credentials.json')
    for i in range(profiles):
        store.set_exchange_credentials(f'bingx:p{i}', {'api_key': f'test-key-{i}', 'api_secret': 'test-secret'})
    return FundingReportService(store, tmp_path / 'funding.json', request_json=request)


def requester(ledger, metadata=None):
    async def request(method, url, **kwargs):
        if url.endswith('/quote/contracts'):
            return page(metadata or [])
        return await ledger(method, url, **kwargs)
    return AsyncMock(side_effect=request)


def test_exact_wire_signing_and_half_open_decimal_aggregation(tmp_path):
    ledger = AsyncMock(return_value=page([
        row(), row('two', income='-0.25'), row('before', time=START_MS - 1),
        row('end', time=END_MS), row('nvda', symbol='NCSKNVDA2USD-USDT', income='-1.5'),
    ]))
    request = requester(ledger)
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [
        ('BingX', 'NCCOXAG2USD', Decimal('0.39710945')),
        ('BingX', 'NCSKNVDA2USD', Decimal('-1.5')),
    ]
    method, url = ledger.call_args.args
    assert method == 'GET' and url == 'https://open-api.bingx.com/openApi/swap/v2/user/income'
    kwargs = ledger.call_args.kwargs
    assert kwargs['headers'] == {'X-BX-APIKEY': 'test-key-0'}
    wire = httpx.Request(method, url, **kwargs).url.query.decode()
    signed, signature = wire.rsplit('&signature=', 1)
    assert signed == '&'.join(sorted(signed.split('&')))
    assert signature == hmac.new(b'test-secret', signed.encode(), hashlib.sha256).hexdigest()
    params = kwargs['params']
    assert params['startTime'] == START_MS and params['endTime'] == END_MS - 1
    assert params['incomeType'] == 'FUNDING_FEE' and params['limit'] == 1000
    assert params['recvWindow'] == 5000
    assert abs(params['timestamp'] - int(datetime.now().timestamp() * 1000)) < 5000


def test_official_labels_and_public_metadata_no_auth_once(tmp_path):
    ledger = AsyncMock(return_value=page([row(), row('nvda', symbol='NCSKNVDA2USD-USDT')]))
    request = requester(ledger, [
        {'symbol': 'NCCOXAG2USD-USDT', 'displayName': 'SILVER(XAG)-USDT'},
        {'symbol': 'NCSKNVDA2USD-USDT', 'displayName': 'NVDA-USDT'},
    ])
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete and {r.symbol for r in report.rows} == {'SILVER(XAG)', 'NVDA'}
    calls = [c for c in request.call_args_list if c.args[1].endswith('/quote/contracts')]
    assert len(calls) == 1
    assert not calls[0].kwargs.get('headers') and not calls[0].kwargs.get('params')


def test_metadata_failure_falls_back_without_losing_funding(tmp_path):
    async def request(method, url, **kwargs):
        if url.endswith('/quote/contracts'):
            raise RuntimeError('private details')
        return page([row()])
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete and report.rows[0].symbol == 'NCCOXAG2USD'


@pytest.mark.parametrize('bad', [
    {'income': 'NaN'}, {'income': 'Infinity'}, {'income': None}, {'income': ''},
    {'income': True}, {'asset': 'USDC'}, {'incomeType': 'REALIZED_PNL'},
    {'time': '123'}, {'time': None}, {'time': True}, {'tranId': ''}, {'tranId': None},
    {'symbol': ''}, {'symbol': 'BTC-USDC'},
])
def test_bad_records_fail_entire_venue_closed(tmp_path, bad):
    request = requester(AsyncMock(return_value=page([row(), row('bad', **bad)])))
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert not report.complete and report.rows == []
    assert 'BingX' in report.warnings[0]


@pytest.mark.parametrize('payload', [[], {}, {'code': 100001, 'msg': 'secret details'},
                                     {'code': False, 'data': []}, {'code': 0, 'data': {}}, page([None])])
def test_invalid_business_payload_fails_closed(tmp_path, payload):
    report = asyncio.run(service(tmp_path, requester(AsyncMock(return_value=payload))).build_report(NOW))
    assert not report.complete and not report.rows
    assert 'secret details' not in '\n'.join(report.warnings)


def test_multiple_profiles_and_duplicate_api_keys(tmp_path):
    request = requester(AsyncMock(return_value=page([row()])))
    s = service(tmp_path, request, profiles=3)
    s.credential_store.set_exchange_credentials('bingx:p2', {'api_key': 'test-key-0', 'api_secret': 'test-secret'})
    report = asyncio.run(s.build_report(NOW))
    assert report.complete and report.total_usd == Decimal('1.29421890')
    assert len([c for c in request.call_args_list if c.args[1].endswith('/user/income')]) == 2


@pytest.mark.parametrize('missing', ['api_key', 'api_secret'])
def test_missing_credentials_fail_closed(tmp_path, missing):
    request = requester(AsyncMock(return_value=page([row()])))
    s = service(tmp_path, request)
    s.credential_store.get_exchange_credentials = lambda ref: {'api_secret' if missing == 'api_key' else 'api_key': 'test'}
    report = asyncio.run(s.build_report(NOW))
    assert not report.complete and not report.rows
    assert not any(c.args[1].endswith('/user/income') for c in request.call_args_list)


def test_partial_profile_failure_isolated_from_other_venues(tmp_path):
    ledger = AsyncMock(side_effect=[page([row()]), RuntimeError('sensitive key and URL')])
    request = requester(ledger)
    s = service(tmp_path, request, profiles=2)
    s._hyperliquid_rows = AsyncMock(return_value=([], ['existing HL warning']))
    report = asyncio.run(s.build_report(NOW))
    assert not report.complete and not report.rows
    assert len(report.warnings) == 2 and report.warnings[0] == 'existing HL warning'
    assert 'sensitive' not in '\n'.join(report.warnings)


@pytest.mark.parametrize('conflict', [False, True])
def test_record_id_deduplication_and_conflicts(tmp_path, conflict):
    request = requester(AsyncMock(return_value=page([row(), row(income='99' if conflict else '0.64710945')])))
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete is not conflict
    assert report.total_usd == (Decimal('0') if conflict else Decimal('0.64710945'))


def test_empty_history_is_complete(tmp_path):
    report = asyncio.run(service(tmp_path, requester(AsyncMock(return_value=page([])))).build_report(NOW))
    assert report.complete and not report.rows


def test_saturated_pages_split_without_order_assumptions_or_parent_double_count(tmp_path, monkeypatch):
    import src.services.bingx_funding as bingx
    sleep = AsyncMock()
    monkeypatch.setattr(bingx, 'sleep', sleep)
    midpoint = (START_MS + END_MS) // 2
    events = [row(str(i), time=START_MS if i < 800 else midpoint, income='0.01') for i in range(1200)]
    async def ledger(method, url, **kwargs):
        params = kwargs['params']
        matching = [r for r in reversed(events) if params['startTime'] <= r['time'] <= params['endTime']]
        return page(matching[:1000])
    request = requester(ledger)
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete and report.total_usd == Decimal('12.00')
    calls = [c for c in request.call_args_list if c.args[1].endswith('/user/income')]
    assert len(calls) == 3
    assert [(c.kwargs['params']['startTime'], c.kwargs['params']['endTime']) for c in calls] == [
        (START_MS, END_MS - 1), (START_MS, midpoint - 1), (midpoint, END_MS - 1)]
    assert sleep.await_count >= 2
    assert all(c.args[0] >= 0.5 for c in sleep.call_args_list)


@pytest.mark.parametrize('budget', [2, 256])
def test_saturation_or_request_budget_fails_closed(tmp_path, monkeypatch, budget):
    import src.services.bingx_funding as bingx
    monkeypatch.setattr(bingx, 'sleep', AsyncMock())
    monkeypatch.setattr(bingx, 'MAX_REQUESTS', budget)
    request = requester(AsyncMock(return_value=page([row(str(i)) for i in range(1000)])))
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert not report.complete and not report.rows
    assert len([c for c in request.call_args_list if c.args[1].endswith('/user/income')]) <= budget


def test_untrusted_origin_never_receives_credentials(tmp_path):
    request = AsyncMock()
    s = service(tmp_path, request)
    s.bingx_api_base = 'https://untrusted.invalid'
    report = asyncio.run(s.build_report(NOW))
    assert not report.complete and not report.rows
    request.assert_not_called()


def test_dependency_wires_bingx_base(monkeypatch):
    import src.deps as deps
    monkeypatch.setattr(deps, 'get_settings', lambda: SimpleNamespace(
        funding_report_state_path='/tmp/not-written.json', hyperliquid_api_base='hl',
        lighter_api_base='lighter', lighter_rh_api_base='lighter-rh', variational_api_base='variational',
        bingx_api_base='https://open-api.bingx.com', okx_api_base='https://www.okx.com',
        ondo_api_base='https://api.ondoperps.xyz',
        aster_api_base='https://fapi.asterdex.com',
        gate_api_base='https://api.gateio.ws/api/v4', gate_settle_currency='usdt',
        woofi_api_base='https://api.orderly.org', mexc_funding_api_base='https://contract.mexc.com',
        arcus_api_base='https://api.arcus.xyz',
        request_timeout_sec=3))
    monkeypatch.setattr(deps, 'get_credential_store', lambda: None)
    deps.get_funding_report_service.cache_clear()
    try:
        assert deps.get_funding_report_service().bingx_api_base == 'https://open-api.bingx.com'
        assert deps.get_funding_report_service().ondo_api_base == 'https://api.ondoperps.xyz'
        assert deps.get_funding_report_service().aster_api_base == 'https://fapi.asterdex.com'
    finally:
        deps.get_funding_report_service.cache_clear()
