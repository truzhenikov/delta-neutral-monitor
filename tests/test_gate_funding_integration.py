import asyncio
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import MSK, FundingReportService

NOW = datetime(2026, 10, 15, 12, tzinfo=MSK)
LOW = 1791966600
BASE = 'https://api.gateio.ws/api/v4'


def service(tmp_path, request):
    store = CredentialStore(tmp_path / 'credentials.json')
    store.set_exchange_credentials('gate:main', {'api_key': 'synthetic', 'api_secret': 'secret'})
    return FundingReportService(store, tmp_path / 'funding.json', request_json=request)


def row(record_id='one', **changes):
    result = dict(id=record_id, contract='BTC_USDT', type='fund', time=LOW, change='-1.25')
    result.update(changes)
    return result


def test_gate_empty_ledger_is_complete_not_fabricated_zero(tmp_path):
    request = AsyncMock(return_value=[])
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete and not report.rows
    request.assert_awaited_once()
    assert request.call_args.args[1] == BASE + '/futures/usdt/account_book'
    assert report.start == datetime(2026, 10, 14, 11, 30, tzinfo=MSK)
    assert report.end == datetime(2026, 10, 15, 11, 30, tzinfo=MSK)


def test_gate_signed_nominal_usd_in_report(tmp_path):
    request = AsyncMock(return_value=[row()])
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [('Gate', 'BTC', Decimal('-1.25'))]


@pytest.mark.parametrize('failure', ['later-page', 'later-profile'])
def test_gate_partial_success_then_failure_withholds_entire_venue(tmp_path, monkeypatch, failure):
    import src.services.gate_funding as funding
    monkeypatch.setattr(funding, 'sleep', AsyncMock())
    gate_calls = []
    async def request(method, url, **kwargs):
        if url.endswith('/info'):
            return [{'time': kwargs['json']['startTime'] + 1, 'delta': {'coin': 'BTC', 'usdc': '2'}}]
        gate_calls.append(kwargs)
        if len(gate_calls) == 1:
            return [row(str(i)) for i in range(100)] if failure == 'later-page' else [row()]
        raise RuntimeError('sensitive key SIGN remote payload')
    s = service(tmp_path, request)
    if failure == 'later-profile':
        s.credential_store.set_exchange_credentials('gate:other', {'api_key': 'other', 'api_secret': 'other-secret'})
    s.credential_store.set_exchange_credentials('hyperliquid:main', {'user_address': '0xabc'})
    report = asyncio.run(s.build_report(NOW))
    assert len(gate_calls) == 2
    assert not report.complete
    assert [(r.exchange, r.amount_usd) for r in report.rows] == [('Hyperliquid', Decimal('2'))]
    assert len(report.warnings) == 1 and 'Gate' in report.warnings[0]
    assert 'sensitive' not in report.warnings[0] and 'SIGN' not in report.warnings[0]


def test_disabled_gate_not_queried(tmp_path):
    request = AsyncMock()
    s = service(tmp_path, request)
    s.credential_store.set_exchange_enabled('gate:main', False)
    report = asyncio.run(s.build_report(NOW))
    assert report.complete and not report.rows
    request.assert_not_called()


def test_dependency_wires_gate_configuration(monkeypatch):
    import src.deps as deps
    settings = SimpleNamespace(
        funding_report_state_path='/tmp/not-written.json', hyperliquid_api_base='hl',
        lighter_api_base='lighter', variational_api_base='variational', bingx_api_base='bingx',
        okx_api_base='okx', ondo_api_base='ondo', aster_api_base='aster',
        gate_api_base=BASE, gate_settle_currency='btc', request_timeout_sec=3,
        woofi_api_base='https://api.orderly.org', mexc_funding_api_base='https://contract.mexc.com',
    )
    monkeypatch.setattr(deps, 'get_settings', lambda: settings)
    monkeypatch.setattr(deps, 'get_credential_store', lambda: None)
    deps.get_funding_report_service.cache_clear()
    try:
        s = deps.get_funding_report_service()
        assert s.gate_api_base == BASE and s.gate_settle_currency == 'btc'
    finally:
        deps.get_funding_report_service.cache_clear()
