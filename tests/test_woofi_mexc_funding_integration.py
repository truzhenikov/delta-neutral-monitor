import asyncio
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.services.credential_store import CredentialStore
from src.services.funding_report_service import MSK, FundingReportService, render_funding_report

NOW = datetime(2026, 10, 16, 3, 30, tzinfo=MSK)
LOW = 1791966600000
CREDS = {
    'woofi': dict(account_id='synthetic', orderly_key='synthetic', orderly_secret='1' * 32),
    'mexc': dict(api_key='synthetic', api_secret='synthetic-secret'),
}


def payload(venue, count=1, page=1):
    if venue == 'woofi':
        rows = [dict(symbol='PERP_BTC_USDC', funding_fee='-1.25', payment_type='Receive',
                     status='Accrued', margin_mode='CROSS', created_time=LOW+i) for i in range(min(count, 60))]
        return dict(success=True, data=dict(rows=rows, meta=dict(total=count, current_page=page, records_per_page=60)))
    rows = [dict(id=i, symbol='BTC_USDT', funding='1.25', settleTime=LOW) for i in range(min(count, 100))]
    return dict(success=True, code=0, data=dict(resultList=rows, totalCount=count,
                currentPage=page, pageSize=100, totalPage=(count+99)//100))


def service(tmp_path, venue, request):
    store = CredentialStore(tmp_path / 'credentials.json')
    store.set_exchange_credentials(venue+':main', CREDS[venue])
    return FundingReportService(store, tmp_path / 'funding.json', request_json=request)


@pytest.mark.parametrize('venue,label', [('woofi', 'WOOFi'), ('mexc', 'MEXC')])
def test_builder_uses_new_ledgers_same_window_and_renderer(tmp_path, venue, label):
    request = AsyncMock(return_value=payload(venue))
    report = asyncio.run(service(tmp_path, venue, request).build_report(NOW))
    assert report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [(label, 'BTC', Decimal('1.25'))]
    assert report.start == datetime(2026, 10, 14, 11, 30, tzinfo=MSK)
    assert report.end == datetime(2026, 10, 15, 11, 30, tzinfo=MSK)
    assert label+' — BTC — +1.25 USD' in render_funding_report(report)
    assert request.call_args.kwargs['follow_redirects'] is False


@pytest.mark.parametrize('venue', ['woofi', 'mexc'])
def test_complete_empty_ledger_has_no_fake_coin_zeros(tmp_path, venue):
    report = asyncio.run(service(tmp_path, venue, AsyncMock(return_value=payload(venue, 0))).build_report(NOW))
    assert report.complete and not report.rows


@pytest.mark.parametrize('venue', ['woofi', 'mexc'])
def test_disabled_profile_is_not_requested(tmp_path, venue):
    request = AsyncMock()
    s = service(tmp_path, venue, request)
    s.credential_store.set_exchange_enabled(venue+':main', False)
    assert asyncio.run(s.build_report(NOW)).complete
    request.assert_not_called()


@pytest.mark.parametrize('venue,label,cap', [('woofi', 'WOOFi', 60), ('mexc', 'MEXC', 100)])
@pytest.mark.parametrize('failure', ['later-page', 'later-profile'])
def test_builder_discards_partial_venue_preserves_other_venues(tmp_path, monkeypatch, venue, label, cap, failure):
    import importlib
    monkeypatch.setattr(importlib.import_module('src.services.'+venue+'_funding'), 'sleep', AsyncMock())
    calls = []

    async def request(method, url, **kwargs):
        if url.endswith('/info'):
            return [dict(time=LOW, delta=dict(coin='ETH', usdc='2'))]
        calls.append(url)
        if len(calls) == 1:
            return payload(venue, cap+1 if failure == 'later-page' else 1)
        raise RuntimeError('sensitive server payload')

    s = service(tmp_path, venue, request)
    s.credential_store.set_exchange_credentials('hyperliquid', dict(user_address='synthetic'))
    if failure == 'later-profile':
        other = dict(CREDS[venue])
        other['account_id' if venue == 'woofi' else 'api_key'] = 'other-synthetic'
        s.credential_store.set_exchange_credentials(venue+':other', other)
    report = asyncio.run(s.build_report(NOW))
    assert len(calls) == 2
    assert not report.complete
    assert [(r.exchange, r.amount_usd) for r in report.rows] == [('Hyperliquid', Decimal('2'))]
    assert len(report.warnings) == 1 and label in report.warnings[0]
    assert 'sensitive' not in render_funding_report(report)


def test_dependencies_explicitly_wire_both_origins(monkeypatch):
    import src.deps as deps
    settings = SimpleNamespace(funding_report_state_path='unused.json', hyperliquid_api_base='hl',
        lighter_api_base='lighter', lighter_rh_api_base='lighter-rh', variational_api_base='var', bingx_api_base='bingx', okx_api_base='okx',
        ondo_api_base='ondo', aster_api_base='aster', gate_api_base='gate', gate_settle_currency='usdt',
        woofi_api_base='https://api.orderly.org', mexc_funding_api_base='https://contract.mexc.com',
        arcus_api_base='https://api.arcus.xyz', request_timeout_sec=3)
    monkeypatch.setattr(deps, 'get_settings', lambda: settings)
    monkeypatch.setattr(deps, 'get_credential_store', lambda: None)
    deps.get_funding_report_service.cache_clear()
    try:
        s = deps.get_funding_report_service()
        assert s.woofi_api_base == settings.woofi_api_base
        assert s.mexc_funding_api_base == settings.mexc_funding_api_base
    finally:
        deps.get_funding_report_service.cache_clear()
