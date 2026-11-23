"""Scope discovery is authenticated, fail-closed and never a primary fallback."""
import asyncio
from pathlib import Path

import httpx
import pytest

from src.connectors import qfex_connector as qf
from src.connectors.real_connectors import RealConnectorRequestError
from src.services.credential_store import CredentialStore
from tests.test_qfex_connector import BALANCE, CREDS, REFDATA, ROW

OTHER = '22222222-2222-4222-8222-222222222222'


def account(identity=CREDS['account_id']):
    return {'account_id': identity, 'is_master': True, 'equity': 1000, 'available_balance': 750}


@pytest.mark.asyncio
@pytest.mark.parametrize('explicit', [False, True])
async def test_discovery_selects_scope_and_signs_positions(monkeypatch, explicit):
    calls = []
    credentials = dict(CREDS) if explicit else {k: CREDS[k] for k in ('api_key', 'api_secret')}
    monkeypatch.setattr(qf, '_runtime_credentials', lambda ref: credentials)
    original = httpx.AsyncClient

    def handler(request):
        calls.append(request)
        if request.url.path == '/user/subaccounts/balance':
            assert 'x-qfex-requested-account-id' not in request.headers
            data = {'accounts': [account(), account(OTHER)] if explicit else [account()]}
        elif request.url.path == '/refdata':
            data = REFDATA
        else:
            assert request.url.path == '/user/positions'
            assert request.headers['x-qfex-requested-account-id'] == CREDS['account_id']
            data = {'balance': BALANCE, 'positions': [ROW]}
        return httpx.Response(200, json=data)

    monkeypatch.setattr(qf.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    connector = qf.QfexRealConnector()
    connector.exchange = 'qfex:test'
    assert (await connector.fetch_account_snapshot()).equity_usd == 1000
    assert [r.url.path for r in calls].count('/user/subaccounts/balance') == 1
    assert 'account_id' in credentials if explicit else 'account_id' not in credentials


@pytest.mark.parametrize('accounts,explicit', [
    ([], False), (None, False), ([account(), account(OTHER)], False),
    ([account('invalid')], False), ([account(None)], False),
    ([account(), account()], False), ([account(OTHER)], True),
    ([dict(account(), is_master='true')], False),
])
def test_ambiguous_invalid_or_foreign_scope_rejected(accounts, explicit):
    credentials = dict(CREDS) if explicit else {k: CREDS[k] for k in ('api_key', 'api_secret')}
    with pytest.raises(RealConnectorRequestError):
        qf.resolve_account(credentials, {'accounts': accounts})


def test_optional_account_id_persists_and_key_only_config(tmp_path):
    store = CredentialStore(tmp_path / 'store.json')
    assert CredentialStore.SUPPORTED_EXCHANGES['qfex'] == ('api_key', 'api_secret')
    assert 'account_id' in CredentialStore.OPTIONAL_EXCHANGE_FIELDS['qfex']
    key_only = {k: CREDS[k] for k in ('api_key', 'api_secret')}
    assert qf.validate_credentials(key_only) == key_only
    store.set_exchange_credentials('qfex:explicit', CREDS)
    store.set_exchange_credentials('qfex:auto', key_only)
    fresh = CredentialStore(store.storage_path)
    assert fresh.get_exchange_credentials('qfex:explicit') == CREDS
    assert fresh.get_exchange_credentials('qfex:auto') == key_only


def test_qfex_funding_history_is_missing_not_zero(tmp_path: Path):
    from src.services.funding_report_service import FundingReportService
    store = CredentialStore(tmp_path / 'credentials.json')
    store.set_exchange_credentials('qfex:main', CREDS)
    from unittest.mock import AsyncMock
    service = FundingReportService(store, tmp_path / 'funding.json', request_json=AsyncMock(side_effect=RuntimeError('history denied')))
    report = asyncio.run(service.build_report())
    assert not report.complete
    assert report.rows == []
    assert any('QFEX' in w and 'полную историю' in w for w in report.warnings)
    store.set_exchange_enabled('qfex:main', False)
    assert asyncio.run(service.build_report()).complete


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['ok', 'ambiguous', 'foreign', 'unauthorized', 'missing_key', 'rotate'])
async def test_setup_two_fields_verifies_before_any_persistence(tmp_path, monkeypatch, mode):
    from src.bot.setup_flow import TelegramSetupFlow
    from src.services.telegram_preferences import TelegramPreferencesService

    store = CredentialStore(tmp_path / 'store.json')
    store.set_exchange_credentials('binance:unrelated', {'api_key': 'other', 'api_secret': 'other'})
    if mode in ('rotate', 'foreign'):
        store.set_exchange_credentials('qfex:desk', CREDS)
        store.set_exchange_enabled('qfex:desk', False)
    before = store.storage_path.read_bytes()
    calls = []
    original = httpx.AsyncClient

    def handler(request):
        calls.append(request)
        if mode == 'unauthorized':
            return httpx.Response(401, text=CREDS['api_secret'])
        if request.url.path == '/user/subaccounts/balance':
            rows = [account(OTHER)] if mode == 'foreign' else [account()]
            if mode in ('ambiguous', 'rotate'):
                rows.append(account(OTHER))
            data = {'accounts': rows}
        elif request.url.path == '/refdata':
            data = REFDATA
        else:
            assert request.headers['x-qfex-requested-account-id'] == CREDS['account_id']
            data = {'balance': BALANCE, 'positions': [ROW]}
        return httpx.Response(200, json=data)

    monkeypatch.setattr(qf.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handler), **kw))
    flow = TelegramSetupFlow(
        preferences=TelegramPreferencesService(tmp_path / 'prefs.json', admin_chat_ids=['1001']),
        credential_store=store,
    )
    assert 'PUBLIC API key' in flow.start_setup('1001', '/setup qfex desk')
    assert 'SECRET API key' in await flow.handle_message_async('1001', CREDS['api_key'])
    if mode == 'missing_key':
        flow._sessions['1001'].payload.pop('api_key')
    reply = await flow.handle_message_async('1001', CREDS['api_secret'])
    assert CREDS['api_secret'] not in reply
    if mode in ('ok', 'rotate'):
        assert 'сохранены' in reply
        assert store.is_exchange_enabled('qfex:desk')
        stored = store.get_exchange_credentials('qfex:desk')
        assert ('account_id' in stored) == (mode == 'rotate')
    else:
        assert 'не сохранён' in reply
        assert store.storage_path.read_bytes() == before
        assert not any(r.url.path == '/user/positions' for r in calls)
    if mode == 'missing_key':
        assert calls == []
