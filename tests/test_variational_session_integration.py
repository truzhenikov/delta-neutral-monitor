import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import httpx
import pytest

from src.connectors import real_connectors
from src.services.alerting import AlertingService
from src.services.variational_funding import fetch_variational_funding
from src.services import variational_sessions as sessions
from tests.test_variational_sessions import NOW, REF, bootstrap, setup, refresh_response, token


def install(tmp_path, monkeypatch):
    store, manager = setup(tmp_path, refresh_response)
    manager.import_file(REF, bootstrap(tmp_path))
    monkeypatch.setattr(sessions, 'VariationalSessions', lambda supplied_store, **kw: manager)
    return store, manager


def test_snapshot_lazy_refresh_and_trusted_origin(tmp_path, monkeypatch):
    from types import SimpleNamespace
    store, manager = install(tmp_path, monkeypatch)
    settings = SimpleNamespace(credential_store_path=str(store.storage_path),
                               variational_api_base=sessions.ORIGIN, variational_vr_token='')
    monkeypatch.setattr(real_connectors, 'get_settings', lambda: settings)
    connector = real_connectors.VariationalRealConnector()
    connector.exchange = REF
    connector._get = AsyncMock(side_effect=[{'balance': '10'}, []])
    assert asyncio.run(connector.fetch_account_snapshot()).equity_usd == 10
    assert connector._get.call_args.kwargs['headers']['Cookie'] == 'vr-token=' + token(NOW + 400, iat=NOW)
    settings.variational_api_base = 'https://evil.invalid'
    connector._get.reset_mock()
    with pytest.raises(ValueError, match='untrusted Variational origin'):
        asyncio.run(connector.fetch_account_snapshot())
    connector._get.assert_not_called()


def test_history_uses_rotated_token_and_disables_redirects(tmp_path, monkeypatch):
    store, _ = install(tmp_path, monkeypatch)
    request = AsyncMock(return_value={'result': [], 'pagination': {'object_count': 0, 'next_page': None}})
    now = datetime.fromtimestamp(NOW, timezone.utc)
    asyncio.run(fetch_variational_funding(store, [REF], now - timedelta(days=1), now, request, sessions.ORIGIN))
    assert request.call_args.kwargs['headers']['Cookie'] == 'vr-token=' + token(NOW + 400, iat=NOW)
    assert request.call_args.kwargs['follow_redirects'] is False


def test_alert_reads_live_expiry_not_store_and_no_healthy_refresh_spam(tmp_path, monkeypatch):
    store, manager = install(tmp_path, monkeypatch)
    manager.token(REF)
    alerts = AlertingService(cooldown_sec=60)
    # Original token is expired, but the managed token has 200s remaining.
    now = datetime.fromtimestamp(NOW + 200, timezone.utc)
    assert alerts.collect_pending_token_expiry_alerts(store, now=now) == []
    now = datetime.fromtimestamp(NOW + 401, timezone.utc)
    result = alerts.collect_pending_token_expiry_alerts(store, now=now)
    assert len(result) == 1 and 'TOKEN EXPIRED' in result[0].text
    assert str(NOW + 400) in result[0].key


def test_managed_expiry_alert_allows_keeper_grace_across_cycles(tmp_path, monkeypatch):
    store, manager = install(tmp_path, monkeypatch)
    alerts = AlertingService(cooldown_sec=60)
    # A healthy session enters the 150s renewal window before the 60s keeper poll.
    for cycle in range(3):
        manager.clock = lambda c=cycle: NOW + c * 300
        if cycle:
            # Model the next successfully renewed JWT without a network call.
            import json
            state = json.loads(manager.path(REF).read_text())
            for cookie in state['cookies']:
                if cookie['name'] == 'vr-token':
                    cookie['value'] = token(NOW + cycle * 300 + 100, iat=NOW + cycle * 300 - 200)
            manager.path(REF).write_text(json.dumps(state))
        now = datetime.fromtimestamp(NOW + cycle * 300, timezone.utc)
        assert alerts.collect_pending_token_expiry_alerts(store, now=now) == []
    # A session still not renewed with only 20s left is genuinely stalled.
    now = datetime.fromtimestamp(NOW + 680, timezone.utc)
    pending = alerts.collect_pending_token_expiry_alerts(store, now=now)
    assert len(pending) == 1 and 'TOKEN EXPIRING' in pending[0].text


def test_keeper_only_enabled_imported_profiles_and_sanitizes_failure(tmp_path, monkeypatch, capsys):
    from src.scripts.variational_sessions import run_once, main
    store, manager = setup(tmp_path, lambda r: httpx.Response(500, text='SECRET'))
    manager.import_file(REF, bootstrap(tmp_path))
    store.set_exchange_credentials('variational:disabled', {'vr_token': token()})
    manager.import_file('variational:disabled', bootstrap(tmp_path))
    store.set_exchange_enabled('variational:disabled', False)
    assert run_once(store, manager) == 1
    assert not store.is_exchange_enabled('variational:disabled')
    assert main(['import', '--profile', REF, '--source', str(tmp_path / 'absent'),
                 '--credential-store', str(store.storage_path)]) == 1
    output = capsys.readouterr()
    assert 'SECRET' not in output.err and 'Traceback' not in output.err
    assert 'unavailable' in output.err


@pytest.mark.parametrize('status', [302, 401, 500])
def test_snapshot_transport_masks_server_body_and_never_redirects(monkeypatch, status):
    from types import SimpleNamespace
    real_client = httpx.AsyncClient
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text='SECRET-COOKIE', headers={'location': 'https://evil.invalid'})
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False
        return real_client(transport=httpx.MockTransport(handler), **kwargs)
    monkeypatch.setattr(httpx, 'AsyncClient', client)
    monkeypatch.setattr(real_connectors, 'get_settings', lambda: SimpleNamespace(request_timeout_sec=15))
    connector = real_connectors.VariationalRealConnector()
    with pytest.raises(real_connectors.RealConnectorRequestError) as error:
        asyncio.run(connector._get(sessions.ORIGIN, '/api/positions', headers={'Cookie': 'SECRET-COOKIE'}))
    assert 'SECRET' not in str(error.value)
    assert len(calls) == 1
