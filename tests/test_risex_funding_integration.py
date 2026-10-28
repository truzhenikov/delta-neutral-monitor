import asyncio
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

import src.services.funding_report_service as reports
from src.services.credential_store import CredentialStore
from src.services.funding_report_service import FundingReportService, render_funding_report
from tests.test_risex_funding import ACCOUNT, BASE, HIGH, LOW, NOW, markets, page, record


def service(tmp_path, request=None, **kwargs):
    store = CredentialStore(tmp_path / 'creds.json')
    store.set_exchange_credentials('risex:main', dict(account=ACCOUNT))
    return FundingReportService(store, tmp_path / 'funding.json', request_json=request, **kwargs)


def test_live_record_raw_http_through_report_and_renderer(tmp_path, monkeypatch):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.method == 'GET'
        assert request.url.host == 'api.rise.trade'
        assert 'authorization' not in request.headers
        if request.url.path == '/v1/markets':
            return httpx.Response(200, json=markets())
        assert request.url.path == '/v1/account/funding-payments'
        assert dict(request.url.params) == dict(account=ACCOUNT, start_time=str(LOW),
            end_time=str(HIGH - 1), page='1', limit='1000', sorted_by='-time')
        return httpx.Response(200, json=page(record()))

    client = httpx.AsyncClient
    monkeypatch.setattr(reports.httpx, 'AsyncClient', lambda **kwargs: client(
        transport=httpx.MockTransport(handler), **kwargs))
    report = asyncio.run(service(tmp_path).build_report(NOW))
    assert not report.complete
    assert any('unsettled' in w and 'settlement' in w for w in report.warnings)
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [
        ('RiseX', 'BZ', Decimal('0.22354902549461375'))]
    assert 'RiseX — BZ — +0.22 USD' in render_funding_report(report)
    assert len(seen) == 2


@pytest.mark.parametrize('later', ['page', 'profile'])
def test_partial_failure_discards_venue_preserves_others_redacted(tmp_path, later):
    n = 0

    async def request(method, url, **kwargs):
        nonlocal n
        if url.endswith('/info'):
            return [dict(time=LOW // 1_000_000, delta=dict(coin='ETH', usdc='2'))]
        if url.endswith('/markets'):
            return markets()
        n += 1
        if n == 1:
            return page(record(), more=(later == 'page'))
        raise RuntimeError('sensitive public account ' + ACCOUNT)

    s = service(tmp_path, request)
    s.credential_store.set_exchange_credentials('hyperliquid', dict(user_address='synthetic'))
    if later == 'profile':
        s.credential_store.set_exchange_credentials('risex:other', dict(account='0x' + 'b' * 40))
    report = asyncio.run(s.build_report(NOW))
    assert n == 2 and not report.complete
    assert [(r.exchange, r.amount_usd) for r in report.rows] == [('Hyperliquid', Decimal('2'))]
    assert len(report.warnings) == 1 and 'RiseX' in report.warnings[0]
    assert ACCOUNT not in render_funding_report(report) and 'sensitive' not in render_funding_report(report)


def test_disabled_no_network_no_mutation(tmp_path):
    request = AsyncMock()
    s = service(tmp_path, request)
    s.credential_store.set_exchange_enabled('risex:main', False)
    before = s.credential_store.storage_path.read_bytes()
    report = asyncio.run(s.build_report(NOW))
    assert report.complete and not report.rows and not report.warnings
    request.assert_not_awaited()
    assert before == s.credential_store.storage_path.read_bytes()


def test_complete_empty_is_not_a_per_coin_zero(tmp_path):
    report = asyncio.run(service(tmp_path, AsyncMock(side_effect=[markets(), page()])).build_report(NOW))
    assert report.complete and report.rows == [] and report.warnings == []


def test_enabled_refs_exact_window_custom_base_forwarded(tmp_path, monkeypatch):
    fetch = AsyncMock(return_value={'BZ': Decimal('1')})
    monkeypatch.setattr(reports, 'fetch_risex_funding', fetch, raising=False)
    s = service(tmp_path, AsyncMock(), risex_api_base=BASE + '/configured')
    s.credential_store.set_exchange_credentials('risex:disabled', dict(account=ACCOUNT))
    s.credential_store.set_exchange_enabled('risex:disabled', False)
    report = asyncio.run(s.build_report(NOW))
    fetch.assert_awaited_once()
    assert fetch.call_args.args[1:4] == (['risex:main'], *s.completed_window(NOW))
    assert fetch.call_args.args[-1] == BASE + '/configured'
    assert report.total_usd == Decimal('1')


@pytest.mark.parametrize('configured', [False, True])
def test_deps_legacy_settings_double_and_configured_origin(tmp_path, monkeypatch, configured):
    import src.deps as deps
    # Exercise the same legacy settings doubles used across earlier venue tests.
    fields = ('hyperliquid', 'lighter', 'lighter_rh', 'variational', 'bingx', 'okx', 'ondo',
              'aster', 'gate', 'woofi', 'arcus', 'binance', 'bitget')
    settings = SimpleNamespace(**{x + '_api_base': BASE for x in fields},
        funding_report_state_path='unused.json', mexc_funding_api_base=BASE,
        gate_settle_currency='usdt', request_timeout_sec=20)
    if configured:
        settings.risex_api_base = BASE + '/configured'
    monkeypatch.setattr(deps, 'get_settings', lambda: settings)
    monkeypatch.setattr(deps, 'get_credential_store', lambda: CredentialStore(tmp_path / 'absent.json'))
    deps.get_funding_report_service.cache_clear()
    try:
        assert deps.get_funding_report_service().risex_api_base == (BASE + '/configured' if configured else BASE)
    finally:
        deps.get_funding_report_service.cache_clear()
