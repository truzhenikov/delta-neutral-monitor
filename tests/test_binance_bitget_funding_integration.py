import asyncio
from datetime import datetime
from decimal import Decimal
from functools import partial
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import src.services.funding_report_service as reports
from src.config import Settings
from src.services.binance_funding import fetch_binance_funding
from src.services.bitget_funding import fetch_bitget_funding
from src.services.credential_store import CredentialStore
from src.services.funding_report_service import MSK, FundingReportService, render_funding_report

NOW = datetime(2026, 10, 16, 12, tzinfo=MSK)
START, END = FundingReportService.completed_window(NOW)
LOW = int(START.timestamp() * 1000)
VENUES = [('binance', 'Binance', 'https://fapi.binance.com'),
          ('bitget', 'Bitget', 'https://api.bitget.com')]
CREDS = dict(api_key='synthetic-key', api_secret='synthetic-secret', api_passphrase='synthetic-passphrase')


def service(tmp_path, venue, request):
    store = CredentialStore(tmp_path / 'credentials.json')
    store.set_exchange_credentials(venue + ':main', CREDS)
    return FundingReportService(store, tmp_path / 'funding.json', request_json=request)


@pytest.mark.parametrize('venue,label,base', VENUES)
def test_real_ledger_reaches_report_and_renderer(tmp_path, monkeypatch, venue, label, base):
    if venue == 'binance':
        payload = [dict(tranId=1, symbol='BTCUSDT', incomeType='FUNDING_FEE',
                        asset='USDT', income='1.25', time=LOW)]
        fetch = fetch_binance_funding
    else:
        payload = dict(code='00000', data=dict(list=[], cursor=''))
        fetch = partial(fetch_bitget_funding, now=NOW, sleep=AsyncMock())
    calls = []

    async def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if venue == 'bitget' and len(calls) == 1:
            return dict(code='00000', data=dict(list=[dict(
                id='1', ts=str(LOW), category='USDT-FUTURES',
                type='CONTRACT_MAIN_SETTLE_FEE_USER_IN', coin='USDT', symbol='BTCUSDT', amount='1.25',
            )], cursor=''))
        return payload

    monkeypatch.setattr(reports, 'fetch_' + venue + '_funding', fetch, raising=False)
    report = asyncio.run(service(tmp_path, venue, request).build_report(NOW))
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [(label, 'BTC', Decimal('1.25'))]
    assert report.complete and not report.warnings
    assert (report.start, report.end) == (START, END)
    assert label + ' — BTC — +1.25 USD' in render_funding_report(report)
    assert all(url.startswith(base + '/') and kwargs['follow_redirects'] is False for _, url, kwargs in calls)
    assert len(calls) == (1 if venue == 'binance' else 8)


@pytest.mark.parametrize('venue,label,base', VENUES)
def test_enabled_refs_window_transport_and_origin_are_forwarded(tmp_path, monkeypatch, venue, label, base):
    request = AsyncMock()
    fetch = AsyncMock(return_value={'ETH': Decimal('-2.5')})
    monkeypatch.setattr(reports, 'fetch_' + venue + '_funding', fetch, raising=False)
    s = service(tmp_path, venue, request)
    s.credential_store.set_exchange_credentials(venue + ':disabled', CREDS)
    s.credential_store.set_exchange_enabled(venue + ':disabled', False)
    report = asyncio.run(s.build_report(NOW))
    fetch.assert_awaited_once()
    store, refs, start, end, transport, origin = fetch.call_args.args
    assert store is s.credential_store
    assert refs == [venue + ':main']
    assert (start, end, origin) == (START, END, base)
    awaitable = transport('GET', base + '/synthetic')
    asyncio.run(awaitable)
    request.assert_awaited_once_with('GET', base + '/synthetic')
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [(label, 'ETH', Decimal('-2.5'))]


@pytest.mark.parametrize('venue,label,base', VENUES)
def test_active_empty_history_has_no_fake_zero_rows(tmp_path, monkeypatch, venue, label, base):
    fetch = AsyncMock(return_value={})
    monkeypatch.setattr(reports, 'fetch_' + venue + '_funding', fetch, raising=False)
    report = asyncio.run(service(tmp_path, venue, AsyncMock()).build_report(NOW))
    fetch.assert_awaited_once()
    assert report.complete and not report.rows and not report.warnings


@pytest.mark.parametrize('venue,label,base', VENUES)
def test_disabled_profiles_never_fetch_or_change_credentials(tmp_path, monkeypatch, venue, label, base):
    fetch = AsyncMock(side_effect=AssertionError('disabled venue must not fetch'))
    request = AsyncMock()
    monkeypatch.setattr(reports, 'fetch_' + venue + '_funding', fetch, raising=False)
    s = service(tmp_path, venue, request)
    s.credential_store.set_exchange_enabled(venue + ':main', False)
    before = s.credential_store.storage_path.read_bytes()
    report = asyncio.run(s.build_report(NOW))
    assert report.complete and not report.rows
    fetch.assert_not_awaited()
    request.assert_not_awaited()
    assert s.credential_store.storage_path.read_bytes() == before
    assert s.credential_store.list_enabled_exchanges() == []


@pytest.mark.parametrize('venue,label,base', VENUES)
@pytest.mark.parametrize('failure', ['later-page', 'later-profile'])
def test_later_failure_withholds_entire_venue_preserves_hyperliquid(tmp_path, monkeypatch, venue, label, base, failure):
    stages = []

    async def fetch(store, refs, start, end, request, api_base):
        partial_totals = {'BTC': Decimal('99')}
        stages.append('first-page-or-profile')
        if failure == 'later-profile':
            assert refs == [venue + ':main', venue + ':other']
        stages.append(failure)
        assert partial_totals
        raise RuntimeError('sensitive credentials and server payload')

    monkeypatch.setattr(reports, 'fetch_' + venue + '_funding', fetch, raising=False)
    request = AsyncMock(return_value=[dict(time=LOW, delta=dict(coin='ETH', usdc='2'))])
    s = service(tmp_path, venue, request)
    s.credential_store.set_exchange_credentials('hyperliquid', dict(user_address='synthetic-address'))
    if failure == 'later-profile':
        s.credential_store.set_exchange_credentials(venue + ':other', dict(CREDS, api_key='synthetic-other'))
    report = asyncio.run(s.build_report(NOW))
    assert stages == ['first-page-or-profile', failure]
    assert not report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [('Hyperliquid', 'ETH', Decimal('2'))]
    assert len(report.warnings) == 1 and label in report.warnings[0]
    assert 'RuntimeError' in report.warnings[0]
    assert 'sensitive' not in render_funding_report(report)


def test_dependency_wiring_uses_explicit_settings_not_constructor_defaults(monkeypatch):
    import src.deps as deps
    settings = SimpleNamespace(
        funding_report_state_path='unused.json', hyperliquid_api_base='hl',
        lighter_api_base='lighter', lighter_rh_api_base='lighter-rh', variational_api_base='var',
        bingx_api_base='bingx', okx_api_base='okx', ondo_api_base='ondo', aster_api_base='aster',
        gate_api_base='gate', gate_settle_currency='usdt', woofi_api_base='woofi',
        mexc_funding_api_base='mexc', arcus_api_base='arcus', request_timeout_sec=3,
        binance_api_base='https://binance.invalid/configured', bitget_api_base='https://bitget.invalid/configured',
    )
    monkeypatch.setattr(deps, 'get_settings', lambda: settings)
    monkeypatch.setattr(deps, 'get_credential_store', lambda: None)
    deps.get_funding_report_service.cache_clear()
    try:
        s = deps.get_funding_report_service()
        assert getattr(s, 'binance_api_base', None) == settings.binance_api_base
        assert getattr(s, 'bitget_api_base', None) == settings.bitget_api_base
    finally:
        deps.get_funding_report_service.cache_clear()


def test_default_origins_and_auto_send_remain_opt_in(tmp_path):
    s = FundingReportService(CredentialStore(tmp_path / 'credentials.json'), tmp_path / 'state.json')
    assert getattr(s, 'binance_api_base', None) == 'https://fapi.binance.com'
    assert getattr(s, 'bitget_api_base', None) == 'https://api.bitget.com'
    assert Settings(_env_file=None).funding_report_auto_send is False
