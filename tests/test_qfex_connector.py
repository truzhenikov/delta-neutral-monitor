"""Documented synthetic QFEX contract fixtures; not private-live evidence."""
import hashlib
import hmac
import json
from types import SimpleNamespace

import httpx
import pytest

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import RealConnectorNotConfiguredError, RealConnectorRequestError
from src.services.credential_store import CredentialStore

CREDS = {'api_key': 'qfex_pub_test', 'api_secret': 'qfex_secret_test',
         'account_id': '11111111-1111-4111-8111-111111111111'}
BALANCE = {'available_balance': 750, 'position_margin': 200, 'order_margin': 50,
           'unrealised_pnl': 100, 'deposit': 900, 'realised_pnl': 0,
           'net_funding': 0, 'builder_rewards': 0}
ROW = {'symbol': 'AAPL-USD', 'position': -10, 'average_price': 100,
       'unrealised_pnl': -100, 'maintenance_margin': .06666666666666667,
       'margin': 73.33333333333333, 'leverage': 10}
REFDATA = {'data': [{'symbol': 'AAPL-USD', 'base_asset': 'AAPL', 'quote_asset': 'USD',
                     'margin_asset': 'USDC', 'isolated_only': False}]}


def test_registry_factory_menu_defaults(tmp_path):
    from src.bot.keyboards import build_setup_exchange_keyboard
    from src.bot.setup_flow import FIELD_LABELS
    from src.config import Settings
    assert CredentialStore.SUPPORTED_EXCHANGES.get('qfex') == ('api_key', 'api_secret', 'account_id')
    assert type(build_connectors(['qfex:desk'])[0]).__name__ == 'QfexRealConnector'
    assert type(build_connectors(['qfex:desk'], True)[0]).__name__ == 'QfexConnector'
    assert build_connectors(['qfex:desk'])[0].exchange == 'qfex:desk'
    assert any(b.callback_data == 'setup_exchange:qfex' for r in build_setup_exchange_keyboard().inline_keyboard for b in r)
    assert ('qfex', 'account_id') in FIELD_LABELS
    defaults = Settings.model_fields['enabled_exchanges'].default.split(',')
    assert 'qfex' not in defaults
    store = CredentialStore(tmp_path / 'missing.json')
    assert 'qfex' not in store.list_enabled_exchanges(defaults)
    assert not store.is_exchange_enabled('qfex')


@pytest.fixture
def setup(monkeypatch):
    from src.connectors import qfex_connector as qf
    monkeypatch.setattr(qf, 'get_settings', lambda: SimpleNamespace(request_timeout_sec=2,
        qfex_api_key='', qfex_api_secret='', qfex_account_id=''))
    monkeypatch.setattr(qf, '_runtime_credentials', lambda ref: CREDS if ref == 'qfex:desk' else {})
    connector = qf.QfexRealConnector()
    connector.exchange = 'qfex:desk'
    return qf, connector


def install(monkeypatch, qf, payload=None, status=200, refdata=None):
    calls = []
    original = httpx.AsyncClient
    def handler(request):
        calls.append(request)
        data = (REFDATA if refdata is None else refdata) if request.url.path == '/refdata' else (
            {'balance': BALANCE, 'positions': [ROW]} if payload is None else payload)
        return httpx.Response(status, content=json.dumps(data), headers={'content-type': 'application/json'})
    monkeypatch.setattr(qf.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    return calls


@pytest.mark.asyncio
async def test_signed_mapping_equity_and_mark_not_last_or_oracle(setup, monkeypatch):
    qf, connector = setup
    calls = install(monkeypatch, qf)
    snapshot = await connector.fetch_account_snapshot()
    assert snapshot.equity_usd == 1000  # not 1100; available + native margin
    assert snapshot.available_margin_usd == 750
    assert snapshot.maintenance_margin_usd == 200  # native position_margin, not dimensionless row rate
    p, = snapshot.positions
    assert (p.side, p.size, p.mark_price, p.entry_price, p.leverage) == ('short', 10, 110, 100, 10)
    assert p.delta_usd == -1100
    assert p.pnl_usd == -100
    assert p.liquidation_price is None
    for request in calls:
        assert request.method == 'GET' and request.url.host == 'api.qfex.com'
        assert request.url.params['api_version'] == '2026-10-29'
        h = request.headers
        if request.url.path == '/refdata':
            assert 'x-qfex-public-key' not in h
        else:
            assert h['x-qfex-public-key'] == CREDS['api_key']
            assert h['x-qfex-requested-account-id'] == CREDS['account_id']
            message = f'{h["x-qfex-nonce"]}:{h["x-qfex-timestamp"]}'.encode()
            assert h['x-qfex-hmac-signature'] == hmac.new(CREDS['api_secret'].encode(), message, hashlib.sha256).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize('qty,upnl,mark,side', [(10,100,110,'long'), (-10,100,90,'short')])
async def test_signed_base_units(setup, monkeypatch, qty, upnl, mark, side):
    qf, connector = setup
    install(monkeypatch, qf, {'balance': BALANCE, 'positions': [dict(ROW, position=qty, unrealised_pnl=upnl)]})
    p, = (await connector.fetch_account_snapshot()).positions
    assert (p.size, p.mark_price, p.side) == (10, mark, side)


@pytest.mark.asyncio
@pytest.mark.parametrize('positions', [[], None])
async def test_documented_flat_account(setup, monkeypatch, positions):
    qf, connector = setup
    install(monkeypatch, qf, {'balance': dict(BALANCE, position_margin=0), 'positions': positions})
    assert (await connector.fetch_account_snapshot()).positions == []


@pytest.mark.asyncio
@pytest.mark.parametrize('field,bad', [('position', None), ('position', True), ('position', 'NaN'),
    ('average_price', 0), ('unrealised_pnl', 'Infinity'), ('leverage', 0),
    ('margin_mode', 'isolated'), ('margin_mode', 'mystery'), ('isolated_collateral', 1),
    ('symbol', 'UNKNOWN-USD'), ('position', '1e1000'), ('position', '1e-1000')])
async def test_bad_positions_fail_closed(setup, monkeypatch, field, bad):
    qf, connector = setup
    install(monkeypatch, qf, {'balance': BALANCE, 'positions': [dict(ROW, **{field: bad})]})
    with pytest.raises(RealConnectorRequestError):
        await connector.fetch_account_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('field,bad', [('available_balance', None), ('position_margin', -1),
    ('order_margin', 'NaN'), ('isolated_equity', 1)])
async def test_bad_balances_fail_closed(setup, monkeypatch, field, bad):
    qf, connector = setup
    install(monkeypatch, qf, {'balance': dict(BALANCE, **{field: bad}), 'positions': [ROW]})
    with pytest.raises(RealConnectorRequestError):
        await connector.fetch_account_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('creds', [{}, {'api_key': 'x'}, dict(CREDS, account_id='not-a-uuid'), dict(CREDS, api_secret='')])
async def test_missing_credentials_never_borrow_profile_or_network(setup, monkeypatch, creds):
    qf, connector = setup
    monkeypatch.setattr(qf, '_runtime_credentials', lambda ref: creds)
    calls = install(monkeypatch, qf)
    with pytest.raises(RealConnectorNotConfiguredError):
        await connector.fetch_account_snapshot()
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [301, 401, 403, 429, 500])
async def test_http_failures_sanitized(setup, monkeypatch, status):
    qf, connector = setup
    install(monkeypatch, qf, status=status)
    with pytest.raises(RealConnectorRequestError, match=f'HTTP {status}') as exc:
        await connector.fetch_account_snapshot()
    assert CREDS['api_secret'] not in str(exc.value)


@pytest.mark.asyncio
async def test_local_credential_validation_is_not_private_verification():
    from src.services.credential_validation import build_default_credential_validation_service
    service = build_default_credential_validation_service(['qfex'])
    bad = await service.validate('qfex', {})
    assert not bad.ok
    good = await service.validate('qfex', CREDS)
    assert good.ok and not good.checked
    assert 'live account verification not performed' in good.message


@pytest.mark.asyncio
async def test_open_positions_require_positive_native_maintenance(setup, monkeypatch):
    qf, connector = setup
    install(monkeypatch, qf, {'balance': dict(BALANCE, position_margin=0), 'positions': [ROW]})
    with pytest.raises(RealConnectorRequestError):
        await connector.fetch_account_snapshot()


@pytest.mark.asyncio
async def test_credential_store_roundtrip_and_masking(tmp_path):
    store = CredentialStore(tmp_path / 'creds.json')
    store.set_exchange_credentials('qfex:desk', CREDS)
    store.set_exchange_enabled('qfex:desk', False)
    store.set_exchange_credentials('binance:other', {'api_key': 'other', 'api_secret': 'other'})
    fresh = CredentialStore(store.storage_path)
    assert fresh.get_exchange_credentials('qfex:desk') == CREDS
    assert not fresh.is_exchange_enabled('qfex:desk')
    assert fresh.get_exchange_credentials_masked('qfex:desk')['api_secret'] != CREDS['api_secret']
    assert store.storage_path.stat().st_mode & 0o777 == 0o600


@pytest.mark.asyncio
async def test_unnamed_environment_fallback_and_named_profile_isolation(setup, monkeypatch):
    qf, connector = setup
    monkeypatch.setattr(qf, 'get_settings', lambda: SimpleNamespace(request_timeout_sec=2,
        qfex_api_key=CREDS['api_key'], qfex_api_secret=CREDS['api_secret'], qfex_account_id=CREDS['account_id']))
    monkeypatch.setattr(qf, '_runtime_credentials', lambda ref: {})
    calls = install(monkeypatch, qf)
    with pytest.raises(RealConnectorNotConfiguredError):
        await connector.fetch_account_snapshot()
    assert not calls
    connector.exchange = 'qfex'
    assert (await connector.fetch_account_snapshot()).equity_usd == 1000


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', ['duplicate', 'missing', 'bad_refdata', 'wrong_currency', 'no_positions'])
async def test_incomplete_or_unsupported_response_is_not_partial_success(setup, monkeypatch, mutation):
    qf, connector = setup
    payload = {'balance': BALANCE, 'positions': [ROW]}
    refdata = REFDATA
    if mutation == 'duplicate':
        payload['positions'] = [ROW, ROW]
    elif mutation == 'missing':
        payload.pop('positions')
    elif mutation == 'bad_refdata':
        refdata = {'data': None}
    elif mutation == 'wrong_currency':
        refdata = {'data': [dict(REFDATA['data'][0], margin_asset='BTC')]}
    elif mutation == 'no_positions':
        payload['positions'] = []
    install(monkeypatch, qf, payload, refdata=refdata)
    with pytest.raises(RealConnectorRequestError):
        await connector.fetch_account_snapshot()


@pytest.mark.asyncio
async def test_json_failures_do_not_expose_details(setup, monkeypatch):
    qf, connector = setup
    original = httpx.AsyncClient
    def handler(request):
        return httpx.Response(200, content='not-json ' + CREDS['api_secret'])
    monkeypatch.setattr(qf.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    with pytest.raises(RealConnectorRequestError) as exc:
        await connector.fetch_account_snapshot()
    assert CREDS['api_secret'] not in str(exc.value)


def test_nonce_changes():
    from src.connectors.qfex_connector import _headers
    first, second = _headers(CREDS), _headers(CREDS)
    assert first['x-qfex-nonce'] != second['x-qfex-nonce']
    assert len(bytes.fromhex(first['x-qfex-nonce'])) == 16


def test_unknown_qfex_liquidation_is_visible_risk():
    from src.core.models import AccountSnapshot, Position, utc_now
    from src.core.risk import RiskEngine
    account = AccountSnapshot('qfex:desk',1000,750,200,
        [Position('qfex:desk','AAPL-USD','long',1,100,100,10,None)],utc_now())
    risk = RiskEngine(.8,4,2000).evaluate([account])
    assert risk.risk_level != 'low'
    assert any('qfex' in w.lower() and 'liquidation' in w.lower() for w in risk.warnings)
