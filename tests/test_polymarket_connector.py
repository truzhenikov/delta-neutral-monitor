"""Raw REST fixtures from official perps/trading.md; not authenticated live data."""
import asyncio
import copy
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from src.connectors import polymarket_connector as pm
from src.connectors.factory import build_connectors
from src.connectors.real_connectors import RealConnectorNotConfiguredError, RealConnectorRequestError
from src.services.credential_store import CredentialStore

CREDS = {'proxy_address': '0x' + '12' * 20, 'proxy_secret': 'synthetic-test-secret'}
POSITION = {'instrument_id': 1, 'symbol': 'BTC-PERP', 'size': '0.01', 'entry_price': '65000',
            'leverage': 5, 'cross': False, 'initial_margin': '130', 'maintenance_margin': '65',
            'position_value': '660', 'liquidation_price': '52000', 'unrealized_pnl': '10',
            'return_on_equity': '0', 'cumulative_funding': '-1'}
PORTFOLIO = {'positions': [POSITION], 'margin': {'total_account_value': '1000',
             'available_order_margin': '870', 'total_initial_margin': '130',
             'total_maintenance_margin': '65', 'total_position_value': '660'},
             'withdrawable': '870', 'in_liquidation': False, 'fee_tier': 0}
TICKER = {'instrument_id': 1, 'symbol': 'BTC-PERP', 'mark_price': '66000', 'last_price': '99999'}


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(pm, 'get_settings', lambda: SimpleNamespace(request_timeout_sec=1,
        polymarket_proxy_address='', polymarket_proxy_secret=''))
    monkeypatch.setattr(pm, '_runtime_credentials', lambda ref: CREDS if ref == 'polymarket:desk' else {})
    c = pm.PolymarketRealConnector()
    c.exchange = 'polymarket:desk'
    return c


def install(monkeypatch, portfolio=None, tickers=None, status=200, delay=0):
    calls = []
    original = httpx.AsyncClient
    account = copy.deepcopy(PORTFOLIO if portfolio is None else portfolio)
    if isinstance(account, dict):
        account.setdefault('timestamp', int(time.time() * 1000))
    quotes = copy.deepcopy([TICKER] if tickers is None else tickers)
    if isinstance(quotes, list):
        for quote in quotes:
            quote.setdefault('timestamp', int(time.time() * 1000))
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(delay)
        return httpx.Response(status, content=json.dumps(account if request.url.path.endswith('portfolio') else quotes),
                              headers={'content-type': 'application/json', 'location': 'https://evil.invalid'})
    monkeypatch.setattr(pm.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    return calls


@pytest.mark.asyncio
async def test_official_wire_mapping_and_mark_not_last(setup, monkeypatch):
    calls = install(monkeypatch)
    s = await setup.fetch_account_snapshot()
    assert (s.exchange, s.equity_usd, s.available_margin_usd, s.maintenance_margin_usd) == ('polymarket:desk', 1000, 870, 65)
    p, = s.positions
    assert (p.side, p.size, p.entry_price, p.mark_price, p.leverage, p.liquidation_price) == ('long', .01, 65000, 66000, 5, 52000)
    assert p.pnl_usd == 10
    assert [r.url.path for r in calls] == ['/v1/account/portfolio', '/v1/info/tickers']
    assert all(r.method == 'GET' and r.url.host == 'api.perpetuals.polymarket.com' for r in calls)
    assert calls[0].headers['polymarket-proxy'] == CREDS['proxy_address']
    assert calls[0].headers['polymarket-secret'] == CREDS['proxy_secret']
    assert 'polymarket-secret' not in calls[1].headers
    assert abs(s.updated_at.timestamp() - time.time()) < 5


@pytest.mark.asyncio
async def test_active_liquidation_fails_closed(setup, monkeypatch):
    install(monkeypatch, dict(PORTFOLIO, in_liquidation=True))
    with pytest.raises(RealConnectorRequestError, match='account in liquidation'):
        await setup.fetch_account_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('liq', [None, '0', 'missing'])
async def test_short_missing_liquidation_and_zero_positions(setup, monkeypatch, liq):
    row = dict(POSITION, size='-0.01', liquidation_price=liq)
    if liq == 'missing':
        row.pop('liquidation_price')
    install(monkeypatch, dict(PORTFOLIO, positions=[row, dict(POSITION, instrument_id=2, symbol='ETH-PERP', size='0')]))
    p, = (await setup.fetch_account_snapshot()).positions
    assert p.side == 'short' and p.delta_usd == -660 and p.liquidation_price is None


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['total_account_value', 'available_order_margin', 'total_initial_margin', 'total_maintenance_margin', 'total_position_value'])
@pytest.mark.parametrize('bad', ['NaN', 'Infinity', 'oops', None, True, '1e1000'])
async def test_invalid_margin(setup, monkeypatch, field, bad):
    account = copy.deepcopy(PORTFOLIO)
    account['margin'][field] = bad
    install(monkeypatch, account)
    with pytest.raises(RealConnectorRequestError):
        await setup.fetch_account_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['size', 'entry_price', 'leverage', 'initial_margin', 'maintenance_margin', 'position_value', 'liquidation_price', 'unrealized_pnl', 'cumulative_funding'])
@pytest.mark.parametrize('bad', ['NaN', 'Infinity', 'oops', True, '1e1000'])
async def test_invalid_position(setup, monkeypatch, field, bad):
    install(monkeypatch, dict(PORTFOLIO, positions=[dict(POSITION, **{field: bad})]))
    with pytest.raises(RealConnectorRequestError):
        await setup.fetch_account_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('account', [[], {}, dict(PORTFOLIO, positions={}), dict(PORTFOLIO, positions=[POSITION, POSITION]),
    dict(PORTFOLIO, in_liquidation='false'), dict(PORTFOLIO, timestamp=1), dict(PORTFOLIO, timestamp=9999999999999)])
async def test_malformed_duplicate_stale_state(setup, monkeypatch, account):
    install(monkeypatch, account)
    with pytest.raises(RealConnectorRequestError):
        await setup.fetch_account_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('quotes', [[], [dict(TICKER, mark_price='NaN')], [TICKER, TICKER], [dict(TICKER, timestamp=1)], [dict(TICKER, symbol='OTHER')]])
async def test_unusable_marks_fail_closed(setup, monkeypatch, quotes):
    install(monkeypatch, tickers=quotes)
    with pytest.raises(RealConnectorRequestError):
        await setup.fetch_account_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [302, 401, 403, 429, 500])
async def test_sanitized_errors_no_redirect(setup, monkeypatch, status):
    calls = install(monkeypatch, {'error': CREDS['proxy_secret']}, status=status)
    with pytest.raises(RealConnectorRequestError) as e:
        await setup.fetch_account_snapshot()
    assert CREDS['proxy_secret'] not in str(e.value)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('creds', [{}, {'proxy_address': CREDS['proxy_address']}, dict(CREDS, proxy_address='bad'),
    dict(CREDS, proxy_secret='x\r\ny'), dict(CREDS, private_key='do-not-accept')])
async def test_credentials_validated_before_network(setup, monkeypatch, creds):
    monkeypatch.setattr(pm, '_runtime_credentials', lambda ref: creds)
    calls = install(monkeypatch)
    with pytest.raises(RealConnectorNotConfiguredError):
        await setup.fetch_account_snapshot()
    assert not calls


@pytest.mark.asyncio
async def test_named_profile_no_environment_fallback(setup, monkeypatch):
    monkeypatch.setattr(pm, '_runtime_credentials', lambda ref: {})
    monkeypatch.setattr(pm, 'get_settings', lambda: SimpleNamespace(request_timeout_sec=1,
        polymarket_proxy_address=CREDS['proxy_address'], polymarket_proxy_secret=CREDS['proxy_secret']))
    calls = install(monkeypatch)
    with pytest.raises(RealConnectorNotConfiguredError):
        await setup.fetch_account_snapshot()
    assert not calls
    setup.exchange = 'polymarket'
    assert (await setup.fetch_account_snapshot()).equity_usd == 1000


@pytest.mark.asyncio
async def test_aggregate_deadline(setup, monkeypatch):
    install(monkeypatch, delay=.06)
    monkeypatch.setattr(pm, 'get_settings', lambda: SimpleNamespace(request_timeout_sec=.1))
    with pytest.raises(RealConnectorRequestError, match='deadline'):
        await setup.fetch_account_snapshot()


@pytest.mark.asyncio
async def test_registry_factory_store_setup_and_disabled_defaults(tmp_path):
    from src.config import Settings
    from src.bot.setup_flow import FIELD_LABELS
    assert CredentialStore.SUPPORTED_EXCHANGES['polymarket'] == ('proxy_address', 'proxy_secret')
    real, = build_connectors(['polymarket:desk'])
    assert isinstance(real, pm.PolymarketRealConnector) and real.exchange == 'polymarket:desk'
    assert real.label == 'Polymarket Perps'
    mock, = build_connectors(['polymarket:desk'], use_mock_data=True)
    assert not isinstance(mock, pm.PolymarketRealConnector)
    assert (await mock.fetch_account_snapshot()).exchange == 'polymarket:desk'
    assert 'proxy' in FIELD_LABELS['polymarket', 'proxy_address']
    defaults = Settings.model_fields['enabled_exchanges'].default.split(',')
    store = CredentialStore(tmp_path / 'missing.json')
    assert 'polymarket' not in store.list_enabled_exchanges(defaults)
    assert not store.is_exchange_enabled('polymarket')
    store.set_exchange_credentials('polymarket:desk', dict(CREDS, private_key='excluded'))
    store.set_exchange_credentials('variational:other', {'vr_token': 'unchanged'})
    assert store.get_exchange_credentials('polymarket:desk') == CREDS
    assert CREDS['proxy_secret'] not in str(store.get_exchange_credentials_masked('polymarket:desk'))
    assert tmp_path.joinpath('missing.json').stat().st_mode & 0o777 == 0o600


def test_endpoint_allowlist():
    with pytest.raises(RealConnectorRequestError):
        asyncio.run(pm.PolymarketRealConnector()._query(None, '/v1/account/proxy', {}))


@pytest.mark.asyncio
async def test_local_credential_validation_does_not_claim_live():
    from src.services.credential_validation import build_default_credential_validation_service
    service = build_default_credential_validation_service(['polymarket'])
    bad = await service.validate('polymarket', dict(CREDS, proxy_secret='bad\nsecret'))
    assert not bad.ok
    good = await service.validate('polymarket', CREDS)
    assert good.ok and not good.checked and 'live' in good.message


@pytest.mark.asyncio
async def test_real_store_named_profile_isolation(tmp_path, monkeypatch):
    from src.connectors import real_connectors
    store = CredentialStore(tmp_path / 'profiles.json')
    other = dict(CREDS, proxy_secret='other-profile-secret')
    store.set_exchange_credentials('polymarket:desk', CREDS)
    store.set_exchange_credentials('polymarket:other', other)
    settings = SimpleNamespace(request_timeout_sec=1, credential_store_path=str(store.storage_path))
    monkeypatch.setattr(pm, 'get_settings', lambda: settings)
    monkeypatch.setattr(real_connectors, 'get_settings', lambda: settings)
    calls = install(monkeypatch)
    for ref in ('polymarket:desk', 'polymarket:other'):
        c = pm.PolymarketRealConnector()
        c.exchange = ref
        assert (await c.fetch_account_snapshot()).exchange == ref
    assert calls[0].headers['polymarket-secret'] == CREDS['proxy_secret']
    assert calls[2].headers['polymarket-secret'] == other['proxy_secret']


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', ['not json', 'null', '{"status":"err","error":"synthetic-test-secret"}'])
async def test_invalid_json_and_error_envelopes(setup, monkeypatch, payload):
    original = httpx.AsyncClient
    monkeypatch.setattr(pm.httpx, 'AsyncClient', lambda **kw: original(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, text=payload)), **kw))
    with pytest.raises(RealConnectorRequestError) as exc:
        await setup.fetch_account_snapshot()
    assert CREDS['proxy_secret'] not in str(exc.value)


@pytest.mark.asyncio
async def test_http_error_message_never_leaks_headers(setup, monkeypatch):
    original = httpx.AsyncClient
    def fail(request):
        raise httpx.ConnectError(CREDS['proxy_secret'], request=request)
    monkeypatch.setattr(pm.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(fail), **kw))
    with pytest.raises(RealConnectorRequestError) as exc:
        await setup.fetch_account_snapshot()
    assert CREDS['proxy_secret'] not in str(exc.value)
