import asyncio
import base64
import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.connectors import backpack_connector as bp
from src.connectors.factory import build_connectors
from src.connectors.real_connectors import RealConnectorNotConfiguredError, RealConnectorRequestError
from src.services.credential_store import CredentialStore

SEED = bytes(range(32))
KEY = Ed25519PrivateKey.from_private_bytes(SEED)
CREDS = {'api_key': base64.b64encode(KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode(),
         'api_secret': base64.b64encode(SEED).decode()}
ACCOUNT = {'netEquity': '1234', 'netEquityAvailable': '1000', 'netExposureFutures': '12000',
           'mmf': '0.05', 'borrowLiability': '0', 'pnlUnrealized': '234',
           'collateral': [{'symbol': 'SOL', 'totalQuantity': '10', 'assetMarkPrice': '100',
                           'balanceNotional': '1000', 'collateralValue': '900', 'collateralWeight': '0.9'}]}
POSITION = {'symbol': 'BTC_USDC_PERP', 'netQuantity': '-0.2', 'entryPrice': '59000',
            'markPrice': '60000', 'estLiquidationPrice': '65000', 'imf': '0.1',
            'mmf': '0.05', 'netExposureNotional': '12000'}


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(bp, 'get_settings', lambda: SimpleNamespace(
        request_timeout_sec=2, backpack_api_base='https://api.backpack.exchange',
        backpack_api_key='', backpack_api_secret=''))
    monkeypatch.setattr(bp, '_runtime_credentials', lambda ref: CREDS if ref == 'backpack:desk' else {})
    connector = bp.BackpackRealConnector()
    connector.exchange = 'backpack:desk'
    return connector


def install(monkeypatch, account=None, positions=None, status=200):
    calls = []
    original = httpx.AsyncClient
    def handler(request):
        calls.append(request)
        payload = (ACCOUNT if account is None else account) if request.url.path.endswith('collateral') else ([POSITION] if positions is None else positions)
        return httpx.Response(status, content=json.dumps(payload), headers={'content-type': 'application/json'})
    monkeypatch.setattr(bp.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    return calls


@pytest.mark.asyncio
async def test_signed_wire_mapping_and_no_upnl_doublecount(setup, monkeypatch):
    calls = install(monkeypatch)
    snapshot = await setup.fetch_account_snapshot()
    assert snapshot.exchange == 'backpack:desk'
    assert snapshot.equity_usd == 1234
    assert snapshot.available_margin_usd == 1000
    assert snapshot.maintenance_margin_usd == 600
    p, = snapshot.positions
    assert (p.side, p.size, p.entry_price, p.mark_price, p.liquidation_price) == ('short', .2, 59000, 60000, 65000)
    assert p.delta_usd == -12000
    for request, instruction in zip(calls, ['collateralQuery', 'positionQuery'], strict=True):
        assert request.method == 'GET'
        assert request.url.host == 'api.backpack.exchange'
        assert not request.url.query
        h = request.headers
        message = f'instruction={instruction}&timestamp={h["X-Timestamp"]}&window={h["X-Window"]}'
        KEY.public_key().verify(base64.b64decode(h['X-Signature']), message.encode())
        assert h['X-API-Key'] == CREDS['api_key']


@pytest.mark.asyncio
async def test_live_sanitized_flat_usdc_and_sol_collateral(setup, monkeypatch):
    # Sanitized authenticated payload supplied by the operator; no keys/identity.
    account = {
        'netEquity': '2.13172746769', 'netEquityAvailable': '2.13172746769',
        'netEquityLocked': '0', 'netExposureFutures': '0', 'pnlUnrealized': '0',
        'unsettledEquity': '0', 'borrowLiability': '0', 'liabilitiesValue': '0',
        'assetsValue': '2.13172746769', 'imf': '0.02', 'mmf': '0.007',
        'marginFraction': None,
        'collateral': [
            {'symbol': 'SOL', 'assetMarkPrice': '105.89', 'balanceNotional': '0.00075203078',
             'collateralValue': '0.000714429241', 'collateralWeight': '0.95',
             'totalQuantity': '0.000007102', 'lendQuantity': '0.000007102', 'availableQuantity': '0'},
            {'symbol': 'USDC', 'assetMarkPrice': '1', 'balanceNotional': '2.131013038449',
             'collateralValue': '2.131013038449', 'collateralWeight': '1',
             'totalQuantity': '2.131013038449', 'lendQuantity': '2.1310130384',
             'availableQuantity': '0.000000000049'},
        ],
    }
    install(monkeypatch, account=account, positions=[])
    snapshot = await setup.fetch_account_snapshot()
    assert snapshot.equity_usd == 2.13172746769
    assert snapshot.available_margin_usd == 2.13172746769
    assert snapshot.maintenance_margin_usd == 0
    assert snapshot.positions == []


def test_settings_and_setup_discovery():
    from src.config import Settings
    from src.bot.setup_flow import FIELD_LABELS
    assert Settings.model_fields['backpack_api_base'].default == 'https://api.backpack.exchange'
    assert 'backpack' not in Settings.model_fields['enabled_exchanges'].default.split(',')
    assert ('backpack', 'api_secret') in FIELD_LABELS


@pytest.mark.asyncio
async def test_long_zero_and_missing_liquidation(setup, monkeypatch):
    row = dict(POSITION, netQuantity='0.2', estLiquidationPrice='0')
    install(monkeypatch, positions=[row, dict(POSITION, netQuantity='0')])
    p, = (await setup.fetch_account_snapshot()).positions
    assert p.side == 'long' and p.liquidation_price is None


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['netEquity', 'netEquityAvailable', 'netExposureFutures', 'mmf', 'borrowLiability'])
@pytest.mark.parametrize('bad', ['NaN', 'Infinity', 'oops', None, True, '1e1000'])
async def test_bad_account_fields_fail_closed(setup, monkeypatch, field, bad):
    account = dict(ACCOUNT, **{field: bad})
    install(monkeypatch, account=account)
    with pytest.raises(RealConnectorRequestError):
        await setup.fetch_account_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('field', ['netQuantity', 'entryPrice', 'markPrice', 'estLiquidationPrice', 'imf'])
@pytest.mark.parametrize('bad', ['NaN', 'Infinity', 'oops', None, True])
async def test_bad_position_fields_fail_closed(setup, monkeypatch, field, bad):
    install(monkeypatch, positions=[dict(POSITION, **{field: bad})])
    with pytest.raises(RealConnectorRequestError):
        await setup.fetch_account_snapshot()


@pytest.mark.asyncio
async def test_unknown_collateral_is_not_one_dollar(setup, monkeypatch):
    account = copy.deepcopy(ACCOUNT)
    account['collateral'][0].pop('assetMarkPrice')
    install(monkeypatch, account=account)
    with pytest.raises(RealConnectorRequestError):
        await setup.fetch_account_snapshot()


@pytest.mark.asyncio
@pytest.mark.parametrize('creds', [{}, {'api_key': CREDS['api_key']}, dict(CREDS, api_secret='bad'),
                                  dict(CREDS, api_secret=base64.b64encode(bytes(32)).decode())])
async def test_missing_malformed_mismatched_keys_before_network(setup, monkeypatch, creds):
    monkeypatch.setattr(bp, '_runtime_credentials', lambda ref: creds)
    calls = install(monkeypatch)
    with pytest.raises(RealConnectorNotConfiguredError):
        await setup.fetch_account_snapshot()
    assert not calls


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [302, 401, 429, 500])
async def test_errors_redacted_and_no_redirect(setup, monkeypatch, status):
    calls = install(monkeypatch, account={'message': CREDS['api_secret']}, status=status)
    with pytest.raises(RealConnectorRequestError) as error:
        await setup.fetch_account_snapshot()
    assert CREDS['api_secret'] not in str(error.value)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_untrusted_origin_rejected(setup, monkeypatch):
    monkeypatch.setattr(bp, 'get_settings', lambda: SimpleNamespace(backpack_api_base='https://evil.invalid'))
    calls = install(monkeypatch)
    with pytest.raises(RealConnectorNotConfiguredError):
        await setup.fetch_account_snapshot()
    assert not calls


def test_registry_factory_profile_and_store(tmp_path):
    assert CredentialStore.SUPPORTED_EXCHANGES['backpack'] == ('api_key', 'api_secret')
    real, = build_connectors(['backpack:desk'])
    assert isinstance(real, bp.BackpackRealConnector) and real.exchange == 'backpack:desk'
    mock, = build_connectors(['backpack:desk'], use_mock_data=True)
    assert not isinstance(mock, bp.BackpackRealConnector)
    store = CredentialStore(tmp_path / 'credentials.json')
    store.set_exchange_credentials('backpack:desk', CREDS)
    store.set_exchange_credentials('variational:existing', {'vr_token': 'unchanged'})
    assert store.get_exchange_credentials('backpack:desk') == CREDS
    assert store.get_exchange_credentials('variational:existing') == {'vr_token': 'unchanged'}


@pytest.mark.asyncio
async def test_default_validation_checks_keypair_without_claiming_live():
    from src.services.credential_validation import build_default_credential_validation_service
    service = build_default_credential_validation_service(['backpack'])
    bad = await service.validate('backpack', dict(CREDS, api_secret='bad'))
    assert not bad.ok
    assert 'bad' not in bad.message
    good = await service.validate('backpack', CREDS)
    assert good.ok and not good.checked
    assert 'live' in good.message.lower()


@pytest.mark.asyncio
async def test_position_notional_overflow_rejected(setup, monkeypatch):
    install(monkeypatch, positions=[dict(POSITION, netQuantity='1e250', markPrice='1e250')])
    with pytest.raises(RealConnectorRequestError):
        await setup.fetch_account_snapshot()


def test_sorted_query_signed_exactly():
    params = {'symbol': 'BTC_USDC_PERP', 'marketType': 'PERP'}
    h = bp.BackpackRealConnector._headers('positionQuery', params, CREDS['api_key'], KEY)
    message = f'instruction=positionQuery&marketType=PERP&symbol=BTC_USDC_PERP&timestamp={h["X-Timestamp"]}&window=5000'
    KEY.public_key().verify(base64.b64decode(h['X-Signature']), message.encode())


@pytest.mark.asyncio
@pytest.mark.parametrize('account,rows', [([], []), ({'error': 'secret'}, []), (ACCOUNT, {}),
                                       (dict(ACCOUNT, borrowLiability='10'), []),
                                       (ACCOUNT, [dict(POSITION, symbol='BTC_USDT_PERP')]),
                                       (ACCOUNT, [POSITION, POSITION])])
async def test_unsupported_or_incomplete_state_rejected(setup, monkeypatch, account, rows):
    install(monkeypatch, account=account, positions=rows)
    with pytest.raises(RealConnectorRequestError):
        await setup.fetch_account_snapshot()


@pytest.mark.asyncio
async def test_aggregate_deadline_includes_spacing(setup, monkeypatch):
    install(monkeypatch)
    setup.request_spacing_sec = 10
    with pytest.raises(RealConnectorRequestError, match='deadline'):
        await asyncio.wait_for(setup.fetch_account_snapshot(), 3)
