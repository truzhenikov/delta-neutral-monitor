"""Synthetic contract tests, not authenticated live accounting evidence."""
import asyncio
from datetime import datetime
from decimal import Decimal, localcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

import src.services.funding_report_service as reports
from src.services.credential_store import CredentialStore
from src.services.funding_report_service import MSK, FundingReportService, render_funding_report

BASE = 'https://api.perpetuals.polymarket.com'
NOW = datetime(2026, 10, 15, 12, tzinfo=MSK)
START, END = FundingReportService.completed_window(NOW)
LOWER = 1791966600000
UPPER = LOWER + 86400000
PROXY = '0x' + 'a' * 40
CREDS = dict(proxy_address=PROXY, proxy_secret='synthetic-only-secret')
OWNER = '0x' + '1' * 40


def identity(owner=OWNER):
    return dict(address=owner, keys=[dict(proxy=PROXY, expiry=UPPER + 1000),
        dict(proxy='0x' + 'b' * 40, expiry=UPPER + 1000)])


def row(**changes):
    result = dict(id=3055723280187747, instrument_id=1, size='10', funding_rate='0.0001',
                  funding_asset='pUSD', funding='1.25', timestamp=LOWER)
    result.update(changes)
    return result


def page(*items, more=False):
    return dict(data=list(items), more=more)


def service(tmp_path, request, **kwargs):
    store = CredentialStore(tmp_path / 'credentials.json')
    store.set_exchange_credentials('polymarket:main', CREDS)
    return FundingReportService(store, tmp_path / 'funding.json', request_json=request, **kwargs)


def test_contract_ledger_closed_positions_exact_money_and_half_open_window(tmp_path):
    request = AsyncMock(side_effect=[identity(), page(
        row(funding='-123456789012345678901234567890.123456789'),
        row(id=2, funding='0.000000001', timestamp=UPPER - 1),
        row(id=3, funding='999', timestamp=UPPER),
        row(id=4, funding='999', timestamp=LOWER - 1)), []])
    with localcontext() as ctx:
        ctx.prec = 3
        report = asyncio.run(service(tmp_path, request).build_report(NOW))
        assert report.total_usd == Decimal('123456789012345678901234567890.123456788')
    assert report.complete and not report.warnings
    assert (report.start, report.end) == (START, END)
    assert len(report.rows) == 1 and report.rows[0].exchange == 'Polymarket'
    assert all(c.args[0] == 'GET' and 'positions' not in c.args[1] for c in request.call_args_list)
    query = parse_qs(urlsplit(request.call_args_list[1].args[1]).query)
    assert query == dict(start_timestamp=[str(LOWER)], end_timestamp=[str(UPPER)])


def test_refs_window_transport_and_configured_base_forwarded(tmp_path, monkeypatch):
    fetch = AsyncMock(return_value={'ETH': Decimal('-2.5')})
    monkeypatch.setattr(reports, 'fetch_polymarket_funding', fetch, raising=False)
    request = AsyncMock()
    s = service(tmp_path, request, polymarket_api_base=BASE + '/configured')
    s.credential_store.set_exchange_credentials('polymarket:disabled', CREDS)
    s.credential_store.set_exchange_enabled('polymarket:disabled', False)
    report = asyncio.run(s.build_report(NOW))
    fetch.assert_awaited_once()
    store, refs, start, end, transport, base = fetch.call_args.args
    assert store is s.credential_store and refs == ['polymarket:main']
    assert (start, end, base) == (START, END, BASE + '/configured')
    asyncio.run(transport('GET', BASE + '/synthetic'))
    request.assert_awaited_once_with('GET', BASE + '/synthetic')
    assert report.total_usd == Decimal('-2.5')


def test_disabled_means_no_collector_credentials_or_requests_or_mutation(tmp_path, monkeypatch):
    fetch = AsyncMock(side_effect=AssertionError('disabled collector'))
    monkeypatch.setattr(reports, 'fetch_polymarket_funding', fetch, raising=False)
    request = AsyncMock()
    s = service(tmp_path, request)
    s.credential_store.set_exchange_enabled('polymarket:main', False)
    before = s.credential_store.storage_path.read_bytes()
    monkeypatch.setattr(s.credential_store, 'get_exchange_credentials', lambda ref: pytest.fail('disabled credential access'))
    report = asyncio.run(s.build_report(NOW))
    assert report.complete and not report.rows and not report.warnings
    fetch.assert_not_awaited()
    request.assert_not_awaited()
    assert s.credential_store.storage_path.read_bytes() == before
    assert s.credential_store.list_enabled_exchanges() == []


@pytest.mark.parametrize('later', ['page', 'profile'])
def test_later_failure_discards_all_venue_rows_preserves_other_venue_redacted(tmp_path, later):
    stages = []

    async def request(method, url, **kwargs):
        if url.endswith('/info'):
            return [dict(time=LOWER, delta=dict(coin='ETH', usdc='2'))]
        stages.append(url)
        if len(stages) == 1:
            return identity()
        if len(stages) == 2:
            return page(row(), more=(later == 'page'))
        if len(stages) == 3:
            return page(row()) if later == 'page' else identity()
        raise RuntimeError('sensitive server body ' + CREDS['proxy_secret'])

    s = service(tmp_path, request)
    s.credential_store.set_exchange_credentials('hyperliquid', dict(user_address='synthetic-address'))
    if later == 'profile':
        s.credential_store.set_exchange_credentials('polymarket:other', dict(
            proxy_address='0x' + 'b' * 40, proxy_secret='synthetic-secondary-secret'))
    report = asyncio.run(s.build_report(NOW))
    assert len(stages) == 4
    assert not report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [('Hyperliquid', 'ETH', Decimal('2'))]
    assert len(report.warnings) == 1 and 'Polymarket' in report.warnings[0]
    text = render_funding_report(report)
    assert 'sensitive' not in text and CREDS['proxy_secret'] not in text and PROXY not in text


def test_complete_empty_history_is_not_synthetic_zero(tmp_path):
    request = AsyncMock(side_effect=[identity(), page()])
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete and not report.rows and not report.warnings
    assert request.await_count == 2


@pytest.mark.parametrize('configured', [False, True])
def test_dependencies_explicit_base_and_legacy_settings_fallback(tmp_path, monkeypatch, configured):
    import src.deps as deps
    settings = SimpleNamespace(
        funding_report_state_path=str(tmp_path / 'state.json'), hyperliquid_api_base='hl',
        lighter_api_base='lighter', lighter_rh_api_base='lighter-rh', variational_api_base='var',
        bingx_api_base='bingx', okx_api_base='okx', ondo_api_base='ondo', aster_api_base='aster',
        gate_api_base='gate', gate_settle_currency='usdt', woofi_api_base='woofi',
        mexc_funding_api_base='mexc', arcus_api_base='arcus', binance_api_base='binance',
        bitget_api_base='bitget', request_timeout_sec=3,
    )
    if configured:
        settings.polymarket_api_base = BASE + '/configured'
    monkeypatch.setattr(deps, 'get_settings', lambda: settings)
    monkeypatch.setattr(deps, 'get_credential_store', lambda: None)
    deps.get_funding_report_service.cache_clear()
    try:
        assert deps.get_funding_report_service().polymarket_api_base == (BASE + '/configured' if configured else BASE)
    finally:
        deps.get_funding_report_service.cache_clear()


def test_constructor_official_default(tmp_path):
    assert service(tmp_path, AsyncMock()).polymarket_api_base == BASE


def test_unknown_account_identity_after_valid_profile_cannot_double_count(tmp_path):
    request = AsyncMock(side_effect=[identity(), page(row()), {}, page(row())])
    s = service(tmp_path, request)
    s.credential_store.set_exchange_credentials('polymarket:other', dict(
        proxy_address='0x' + 'b' * 40, proxy_secret='synthetic-secondary-secret'))
    report = asyncio.run(s.build_report(NOW))
    assert not report.complete and not report.rows
    assert request.await_count == 3


def test_bad_later_alias_credentials_preflight_zero_requests(tmp_path):
    request = AsyncMock()
    s = service(tmp_path, request)
    s.credential_store.set_exchange_credentials('polymarket:other', dict(CREDS, proxy_secret='different'))
    report = asyncio.run(s.build_report(NOW))
    assert not report.complete and not report.rows
    request.assert_not_called()


@pytest.mark.parametrize('status', [200, 307, 401, 429, 500])
def test_real_http_transport_preserves_large_ids_money_and_blocks_redirects(tmp_path, monkeypatch, status):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == 'GET' and request.url.host == 'api.perpetuals.polymarket.com'
        if request.url.path == '/v1/account/credentials':
            assert request.headers['polymarket-proxy'] == PROXY
            assert request.headers['polymarket-secret'] == CREDS['proxy_secret']
            return httpx.Response(status, json=identity(), headers={'location': 'https://foreign.invalid/private'})
        if request.url.path == '/v1/account/funding':
            assert dict(request.url.params) == dict(start_timestamp=str(LOWER), end_timestamp=str(UPPER))
            return httpx.Response(200, json=page(row(id=9007199254740993123456789, funding='-2.50')))
        assert request.url.path == '/v1/info/instruments'
        assert 'polymarket-proxy' not in request.headers and 'polymarket-secret' not in request.headers
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(reports.httpx, 'AsyncClient', lambda **kwargs: client(transport=transport, **kwargs))
    report = asyncio.run(service(tmp_path, None).build_report(NOW))
    if status == 200:
        assert report.complete and report.total_usd == Decimal('2.50') and len(seen) == 3
    else:
        assert not report.complete and not report.rows and len(seen) == 1
        assert CREDS['proxy_secret'] not in render_funding_report(report)
