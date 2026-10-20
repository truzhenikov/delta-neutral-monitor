import asyncio
import base64
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

import src.services.funding_report_service as reports
from src.services.credential_store import CredentialStore
from src.services.funding_report_service import MSK, FundingReportService, render_funding_report

BASE = 'https://api.backpack.exchange'
NOW = datetime(2026, 10, 15, 12, tzinfo=MSK)
START, END = FundingReportService.completed_window(NOW)
SEED = b'a' * 32
CREDS = dict(api_key=base64.b64encode(Ed25519PrivateKey.from_private_bytes(SEED).public_key().public_bytes(
    Encoding.Raw, PublicFormat.Raw)).decode(), api_secret=base64.b64encode(SEED).decode())


def row(**changes):
    result = dict(userId=123, subaccountId=0, symbol='BTC_USDC_PERP', quantity='1.25',
                  intervalEndTimestamp='2026-10-14T08:30:00', fundingRate='0.0001')
    result.update(changes)
    return result


def service(tmp_path, request, **kwargs):
    store = CredentialStore(tmp_path / 'credentials.json')
    store.set_exchange_credentials('backpack:main', CREDS)
    return FundingReportService(store, tmp_path / 'funding.json', request_json=request, **kwargs)


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    import src.services.backpack_funding as ledger
    monkeypatch.setattr(ledger, 'sleep', AsyncMock())


def test_actual_ledger_report_renderer_and_half_open_window(tmp_path):
    request = AsyncMock(side_effect=[[
        row(), row(symbol='ETH_USDC_PERP', quantity='-2.50'),
        row(intervalEndTimestamp='2026-10-15T08:30:00', quantity='999'),
        row(intervalEndTimestamp='2026-10-14T08:29:59.999999999', quantity='999'),
    ], []])
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    assert report.complete and not report.warnings
    assert (report.start, report.end) == (START, END)
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [
        ('Backpack', 'BTC', Decimal('1.25')), ('Backpack', 'ETH', Decimal('-2.50'))]
    assert report.total_usd == Decimal('-1.25')
    text = render_funding_report(report)
    assert 'Backpack — BTC — +1.25 USD' in text
    assert 'Backpack — ETH — -2.50 USD' in text
    assert 'Итого — -1.25 USD' in text
    assert request.await_count == 2
    assert all(call.args[0] == 'GET' and '/history/funding?' in call.args[1] for call in request.call_args_list)


def test_refs_window_transport_and_configured_base_forwarded(tmp_path, monkeypatch):
    fetch = AsyncMock(return_value={'ETH': Decimal('-2.5')})
    monkeypatch.setattr(reports, 'fetch_backpack_funding', fetch, raising=False)
    request = AsyncMock()
    s = service(tmp_path, request, backpack_api_base=BASE + '/configured')
    s.credential_store.set_exchange_credentials('backpack:disabled', CREDS)
    s.credential_store.set_exchange_enabled('backpack:disabled', False)
    report = asyncio.run(s.build_report(NOW))
    fetch.assert_awaited_once()
    store, refs, start, end, transport, base = fetch.call_args.args
    assert store is s.credential_store and refs == ['backpack:main']
    assert (start, end, base) == (START, END, BASE + '/configured')
    asyncio.run(transport('GET', BASE + '/synthetic'))
    request.assert_awaited_once_with('GET', BASE + '/synthetic')
    assert report.total_usd == Decimal('-2.5')


def test_disabled_means_no_collector_credentials_or_requests_and_no_mutation(tmp_path, monkeypatch):
    fetch = AsyncMock(side_effect=AssertionError('disabled collector'))
    monkeypatch.setattr(reports, 'fetch_backpack_funding', fetch, raising=False)
    request = AsyncMock()
    s = service(tmp_path, request)
    s.credential_store.set_exchange_enabled('backpack:main', False)
    before = s.credential_store.storage_path.read_bytes()
    monkeypatch.setattr(s.credential_store, 'get_exchange_credentials', lambda ref: pytest.fail('disabled credentials read'))
    report = asyncio.run(s.build_report(NOW))
    assert report.complete and not report.rows and not report.warnings
    fetch.assert_not_awaited()
    request.assert_not_awaited()
    assert s.credential_store.storage_path.read_bytes() == before
    assert s.credential_store.list_enabled_exchanges() == []


@pytest.mark.parametrize('later', ['page', 'profile'])
def test_actual_later_failure_discards_entire_venue_keeps_other_rows_redacted(tmp_path, later):
    stages = []

    async def request(method, url, **kwargs):
        if url.endswith('/info'):
            return [dict(time=int(START.timestamp() * 1000), delta=dict(coin='ETH', usdc='2'))]
        stages.append(url)
        if len(stages) == 1:
            return [row(quantity='999')]
        if later == 'profile' and len(stages) == 2:
            return []
        raise RuntimeError('sensitive server body ' + CREDS['api_secret'])

    s = service(tmp_path, request)
    s.credential_store.set_exchange_credentials('hyperliquid', dict(user_address='synthetic-address'))
    if later == 'profile':
        seed = b'b' * 32
        s.credential_store.set_exchange_credentials('backpack:other', dict(
            api_key=base64.b64encode(Ed25519PrivateKey.from_private_bytes(seed).public_key().public_bytes(
                Encoding.Raw, PublicFormat.Raw)).decode(), api_secret=base64.b64encode(seed).decode()))
    report = asyncio.run(s.build_report(NOW))
    assert len(stages) == (2 if later == 'page' else 3)
    assert not report.complete
    assert [(r.exchange, r.symbol, r.amount_usd) for r in report.rows] == [('Hyperliquid', 'ETH', Decimal('2'))]
    assert len(report.warnings) == 1 and 'Backpack' in report.warnings[0]
    text = render_funding_report(report)
    assert 'sensitive' not in text and CREDS['api_secret'] not in text and CREDS['api_key'] not in text


def test_complete_empty_history_is_not_synthetic_zero(tmp_path):
    request = AsyncMock(return_value=[])
    report = asyncio.run(service(tmp_path, request).build_report(NOW))
    request.assert_awaited_once()
    assert report.complete and not report.rows and not report.warnings


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
        settings.backpack_api_base = BASE + '/configured'
    monkeypatch.setattr(deps, 'get_settings', lambda: settings)
    monkeypatch.setattr(deps, 'get_credential_store', lambda: None)
    deps.get_funding_report_service.cache_clear()
    try:
        assert deps.get_funding_report_service().backpack_api_base == (BASE + '/configured' if configured else BASE)
    finally:
        deps.get_funding_report_service.cache_clear()


def test_constructor_official_default(tmp_path):
    assert service(tmp_path, AsyncMock()).backpack_api_base == BASE
