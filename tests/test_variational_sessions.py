"""Synthetic sessions only; never load operator bootstrap/credentials."""
import base64
import json
import multiprocessing
import os
import time
from http.cookiejar import Cookie

import httpx
import pytest

from src.services.credential_store import CredentialStore
from src.services import variational_sessions as sessions

NOW = 2_000_000_000
ADDRESS = '0x' + 'a' * 40
REF = 'variational:main'


def token(exp=NOW + 100, address=ADDRESS, company='company-a', iat=NOW - 200):
    body = base64.urlsafe_b64encode(json.dumps(dict(address=address, company=company, exp=exp, iat=iat)).encode()).decode().rstrip('=')
    return f'header.{body}.signature'


def cookie(name, value, domain='omni.variational.io', path='/'):
    return vars(Cookie(0, name, value, None, False, domain, domain.startswith('.'),
                       domain.startswith('.'), path, True, True, None, True, None, None, {}, False))


def bootstrap(tmp_path, **kwargs):
    jar = [cookie('vr-token', token(**kwargs)), cookie('vr-connected-address', kwargs.get('address', ADDRESS)),
           cookie('vr-ll-token', 'long-lived'), cookie('vr-ll-token-' + kwargs.get('address', ADDRESS), 'scoped'),
           cookie('vr-ll-token-0xforeign', 'foreign'), cookie('unrelated', 'foreign'),
           cookie('__cf_bm', 'cloud', '.variational.io')]
    p = tmp_path / 'bootstrap.json'
    p.write_text(json.dumps(dict(headers={'User-Agent': 'Mozilla/5.0 test', 'Cookie': 'never copy'},
                                cookies=jar, original_exp=NOW + 100, response_body='{}')))
    p.chmod(0o600)
    return p


def setup(tmp_path, handler=None):
    store = CredentialStore(tmp_path / 'credentials' / 'exchanges.json')
    store.set_exchange_credentials(REF, {'vr_token': token()})
    manager = sessions.VariationalSessions(store, clock=lambda: NOW,
        transport=httpx.MockTransport(handler) if handler else None)
    return store, manager


def refresh_response(request):
    assert str(request.url) == sessions.ORIGIN + '/api/auth/refresh'
    assert request.method == 'POST' and request.content == b''
    assert request.headers['vr-connected-address'] == ADDRESS
    assert request.headers['origin'] == sessions.ORIGIN
    assert 'foreign' not in request.headers['cookie']
    return httpx.Response(200, json={}, headers=[
        ('set-cookie', f'vr-token={token(NOW + 400, iat=NOW)}; Path=/; Secure; HttpOnly'),
        ('set-cookie', 'vr-ll-token=rotated; Path=/; Secure; HttpOnly'),
        ('set-cookie', '__cf_bm=; Max-Age=0; Domain=.variational.io; Path=/')])


def test_import_preserves_lowercase_browser_user_agent(tmp_path):
    _, manager = setup(tmp_path)
    source = bootstrap(tmp_path)
    data = json.loads(source.read_text())
    data['headers'] = {'user-agent': 'Mozilla/5.0 exact-browser'}
    source.write_text(json.dumps(data))
    manager.import_file(REF, source)
    state = json.loads(manager.path(REF).read_text())
    assert state['user_agent'] == 'Mozilla/5.0 exact-browser'


def test_static_fallback_and_disabled_no_network(tmp_path):
    store, manager = setup(tmp_path, lambda r: pytest.fail('network'))
    assert manager.token(REF) == token()
    manager.import_file(REF, bootstrap(tmp_path))
    store.set_exchange_enabled(REF, False)
    assert manager.token(REF) == token()
    assert not store.is_exchange_enabled(REF)


def test_rotation_restart_scopes_deletion_permissions_and_no_duplicate_refresh(tmp_path):
    calls = []
    def handler(request):
        calls.append(request)
        return refresh_response(request)
    store, manager = setup(tmp_path, handler)
    before = store.storage_path.read_bytes()
    manager.import_file(REF, bootstrap(tmp_path))
    assert manager.token(REF) == token(NOW + 400, iat=NOW)
    assert manager.token(REF) == token(NOW + 400, iat=NOW)
    restarted = sessions.VariationalSessions(store, clock=lambda: NOW, transport=httpx.MockTransport(handler))
    assert restarted.token(REF) == token(NOW + 400, iat=NOW)
    assert len(calls) == 1
    data = json.loads(manager.path(REF).read_text())
    assert {c['name'] for c in data['cookies']} == {'vr-token', 'vr-connected-address', 'vr-ll-token', 'vr-ll-token-' + ADDRESS}
    assert next(c for c in data['cookies'] if c['name'] == 'vr-ll-token')['value'] == 'rotated'
    assert os.stat(manager.path(REF)).st_mode & 0o777 == 0o600
    assert os.stat(manager.root).st_mode & 0o777 == 0o700
    assert store.storage_path.read_bytes() == before


@pytest.mark.parametrize('candidate', [token(), token(NOW + 400, company='wrong', iat=NOW),
    token(NOW + 400, address='0x' + 'b' * 40, iat=NOW), 'malformed'])
def test_bad_rotation_not_published_and_backoff_survives_restart(tmp_path, candidate):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={}, headers={'set-cookie': f'vr-token={candidate}; Path=/; Secure'})
    store, manager = setup(tmp_path, handler)
    manager.import_file(REF, bootstrap(tmp_path))
    for m in [manager, sessions.VariationalSessions(store, clock=lambda: NOW, transport=httpx.MockTransport(handler))]:
        with pytest.raises(sessions.SessionError, match='Variational session unavailable'):
            m.token(REF)
    assert len(calls) == 1
    assert manager.token(REF, refresh=False) == token()


@pytest.mark.parametrize('status', [302, 400, 500])
def test_http_errors_masked_and_redirect_never_followed(tmp_path, status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, text='SECRET', headers={'location': 'https://evil.invalid'})
    _, manager = setup(tmp_path, handler)
    manager.import_file(REF, bootstrap(tmp_path))
    with pytest.raises(sessions.SessionError) as error:
        manager.token(REF)
    assert 'SECRET' not in str(error.value) and error.value.__cause__ is None
    assert len(calls) == 1


def test_import_identity_permissions_and_profile_isolation(tmp_path):
    store, manager = setup(tmp_path)
    p = bootstrap(tmp_path, company='wrong')
    with pytest.raises(sessions.SessionError):
        manager.import_file(REF, p)
    p = bootstrap(tmp_path)
    p.chmod(0o644)
    with pytest.raises(sessions.SessionError):
        manager.import_file(REF, p)
    p.chmod(0o600)
    manager.import_file(REF, p)
    store.set_exchange_credentials('variational:other', {'vr_token': token(company='other')})
    assert manager.token('variational:other') == token(company='other')
    with pytest.raises(sessions.SessionError):
        manager.import_file('variational:other', p)
    with pytest.raises(sessions.SessionError):
        manager.path('variational:../../escape')
    with pytest.raises(sessions.SessionError):
        sessions.VariationalSessions(store, origin='http://omni.variational.io')


def _worker(store_path, count, gate, results):
    store = CredentialStore(store_path)
    def handler(request):
        with count.get_lock():
            count.value += 1
        time.sleep(.15)
        return refresh_response(request)
    m = sessions.VariationalSessions(store, clock=lambda: NOW, transport=httpx.MockTransport(handler))
    gate.wait()
    results.put(m.token(REF) == token(NOW + 400, iat=NOW))


def test_two_processes_reload_under_lock_and_refresh_once(tmp_path):
    store, manager = setup(tmp_path)
    manager.import_file(REF, bootstrap(tmp_path))
    ctx = multiprocessing.get_context('fork')
    count, gate, results = ctx.Value('i', 0), ctx.Event(), ctx.Queue()
    workers = [ctx.Process(target=_worker, args=(store.storage_path, count, gate, results)) for _ in range(2)]
    for worker in workers:
        worker.start()
    gate.set()
    for worker in workers:
        worker.join(10)
        assert worker.exitcode == 0
    assert all(results.get(timeout=1) for _ in workers)
    assert count.value == 1


def test_auth_cookie_deletion_and_new_path_scope_survive_reload(tmp_path):
    def handler(request):
        return httpx.Response(200, json={}, headers=[
            ('set-cookie', f'vr-token={token(NOW + 400, iat=NOW)}; Path=/; Secure'),
            ('set-cookie', 'vr-ll-token=; Max-Age=0; Path=/'),
            ('set-cookie', 'cf_clearance=scoped-clearance; Domain=.variational.io; Path=/api; Secure')])
    store, manager = setup(tmp_path, handler)
    manager.import_file(REF, bootstrap(tmp_path))
    assert manager.token(REF) == token(NOW + 400, iat=NOW)
    saved = json.loads(manager.path(REF).read_text())['cookies']
    assert not any(c['name'] == 'vr-ll-token' for c in saved)
    clearance = next(c for c in saved if c['name'] == 'cf_clearance')
    assert (clearance['domain'], clearance['path']) == ('.variational.io', '/api')
    restarted = sessions.VariationalSessions(store, clock=lambda: NOW)
    assert restarted.token(REF) == token(NOW + 400, iat=NOW)


def test_readonly_alert_accessor_does_not_wait_for_refresh_lock(tmp_path, monkeypatch):
    _, manager = setup(tmp_path)
    manager.import_file(REF, bootstrap(tmp_path))
    monkeypatch.setattr(manager, '_lock', lambda ref: pytest.fail('readonly must not lock'))
    assert manager.token(REF, refresh=False) == token()


def test_broken_session_symlink_fails_closed_not_static_fallback(tmp_path):
    _, manager = setup(tmp_path)
    manager.root.mkdir(parents=True)
    manager.path(REF).symlink_to(tmp_path / 'absent')
    with pytest.raises(sessions.SessionError):
        manager.token(REF)


@pytest.mark.parametrize('changes', [{'domain': 'evil.invalid'}, {'path': 'relative'},
    {'secure': False}, {'value': '0x' + 'b' * 40}])
def test_invalid_connected_cookie_rejected_on_disk(tmp_path, changes):
    _, manager = setup(tmp_path)
    manager.import_file(REF, bootstrap(tmp_path))
    state = json.loads(manager.path(REF).read_text())
    next(c for c in state['cookies'] if c['name'] == 'vr-connected-address').update(changes)
    manager.path(REF).write_text(json.dumps(state))
    with pytest.raises(sessions.SessionError):
        manager.token(REF, refresh=False)


def test_missing_required_session_cookie_fails_closed(tmp_path):
    _, manager = setup(tmp_path)
    manager.import_file(REF, bootstrap(tmp_path))
    state = json.loads(manager.path(REF).read_text())
    state['cookies'] = [c for c in state['cookies'] if c['name'] != 'vr-token']
    manager.path(REF).write_text(json.dumps(state))
    with pytest.raises(sessions.SessionError):
        manager.token(REF, refresh=False)


@pytest.mark.parametrize('failure', ['http', 'json', 'jwt'])
def test_failed_refresh_persists_cookie_changes_without_publishing_token(tmp_path, failure):
    candidate = 'malformed' if failure == 'jwt' else token(NOW + 400, iat=NOW)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) > 1:
            assert 'vr-ll-token=rotated' in request.headers['cookie']
            assert '__cf_bm=' not in request.headers['cookie']
            assert 'cf_clearance=clearance' in request.headers['cookie']
            assert f'vr-token={token()}' in request.headers['cookie']
            return refresh_response(request)
        return httpx.Response(500 if failure == 'http' else 200,
            content=b'not json' if failure == 'json' else b'{}', headers=[
                ('set-cookie', f'vr-token={candidate}; Path=/; Secure'),
                ('set-cookie', 'vr-connected-address=; Max-Age=0; Path=/' if failure == 'http'
                 else f'vr-connected-address={ADDRESS}; Path=/; Secure'),
                ('set-cookie', 'vr-ll-token=rotated; Path=/; Secure; HttpOnly'),
                ('set-cookie', f'vr-ll-token-{ADDRESS}=; Max-Age=0; Path=/'),
                ('set-cookie', '__cf_bm=; Max-Age=0; Domain=.variational.io; Path=/'),
                ('set-cookie', 'cf_clearance=clearance; Domain=.variational.io; Path=/api; Secure')])

    store, manager = setup(tmp_path, handler)
    manager.import_file(REF, bootstrap(tmp_path))
    with pytest.raises(sessions.SessionError, match='^Variational session unavailable$'):
        manager.token(REF)
    saved = json.loads(manager.path(REF).read_text())
    values = {c['name']: c['value'] for c in saved['cookies']}
    assert values['vr-ll-token'] == 'rotated'
    assert values['vr-token'] == token()
    assert values['vr-connected-address'] == ADDRESS
    assert '__cf_bm' not in values and 'vr-ll-token-' + ADDRESS not in values
    assert values['cf_clearance'] == 'clearance'
    assert saved['failures'] == 1 and saved['retry_at'] == NOW + 30
    assert manager.token(REF, refresh=False) == token()
    restarted = sessions.VariationalSessions(store, clock=lambda: NOW + 31,
        transport=httpx.MockTransport(handler))
    assert restarted.token(REF) == token(NOW + 400, iat=NOW)
    assert len(calls) == 2


@pytest.mark.parametrize('foreign_cookie', [
    f'vr-token={token(NOW + 400, company="foreign", iat=NOW)}; Path=/; Secure',
    f'vr-token={token(NOW + 400, address="0x" + "b" * 40, iat=NOW)}; Path=/; Secure',
    'vr-connected-address=0x' + 'b' * 40 + '; Path=/; Secure',
])
def test_foreign_response_identity_does_not_mix_refresh_cookies(tmp_path, foreign_cookie):
    def handler(request):
        return httpx.Response(500, json={}, headers=[
            ('set-cookie', foreign_cookie),
            ('set-cookie', 'vr-ll-token=foreign-session; Path=/; Secure'),
            ('set-cookie', '__cf_bm=; Max-Age=0; Domain=.variational.io; Path=/')])
    _, manager = setup(tmp_path, handler)
    manager.import_file(REF, bootstrap(tmp_path))
    before = json.loads(manager.path(REF).read_text())['cookies']
    with pytest.raises(sessions.SessionError):
        manager.token(REF)
    after = json.loads(manager.path(REF).read_text())
    assert after['cookies'] == before
    assert after['failures'] == 1
    assert manager.token(REF, refresh=False) == token()


def test_failed_refresh_sanitizes_cookie_scopes(tmp_path):
    def handler(request):
        return httpx.Response(500, json={}, headers=[
            ('set-cookie', 'vr-ll-token=insecure; Path=/'),
            ('set-cookie', f'vr-ll-token-{ADDRESS}=broad; Domain=.variational.io; Path=/; Secure'),
            ('set-cookie', 'cf_clearance=foreign; Domain=evil.invalid; Path=/; Secure'),
            ('set-cookie', 'unrelated=excluded; Path=/; Secure'),
            ('set-cookie', 'vr-ll-token-0xforeign=excluded; Path=/; Secure'),
            ('set-cookie', '_cfuvid=valid; Domain=.variational.io; Path=/api; Secure')])
    _, manager = setup(tmp_path, handler)
    manager.import_file(REF, bootstrap(tmp_path))
    with pytest.raises(sessions.SessionError):
        manager.token(REF)
    saved = json.loads(manager.path(REF).read_text())['cookies']
    assert {c['name'] for c in saved} == {
        'vr-token', 'vr-connected-address', 'vr-ll-token', 'vr-ll-token-' + ADDRESS,
        '__cf_bm', '_cfuvid'}
    assert next(c for c in saved if c['name'] == 'vr-ll-token')['value'] == 'long-lived'
    assert next(c for c in saved if c['name'] == '_cfuvid')['path'] == '/api'
    assert manager.token(REF, refresh=False) == token()


def test_profile_mismatch_fails_closed_before_refresh(tmp_path):
    _, manager = setup(tmp_path, lambda request: pytest.fail('must not refresh mismatched profile'))
    manager.import_file(REF, bootstrap(tmp_path))
    saved = json.loads(manager.path(REF).read_text())
    saved['profile'] = 'variational:other'
    manager.path(REF).write_text(json.dumps(saved))
    before = manager.path(REF).read_bytes()
    with pytest.raises(sessions.SessionError):
        manager.token(REF)
    assert manager.path(REF).read_bytes() == before



def test_foreign_secondary_access_cookie_rejects_entire_response(tmp_path):
    def handler(request):
        return httpx.Response(200, json={}, headers=[
            ('set-cookie', f'vr-token={token(NOW + 400, iat=NOW)}; Path=/; Secure'),
            ('set-cookie', f'vr-token={token(NOW + 400, company="foreign", iat=NOW)}; Path=/api; Secure'),
            ('set-cookie', 'vr-ll-token=foreign-session; Path=/; Secure')])
    _, manager = setup(tmp_path, handler)
    manager.import_file(REF, bootstrap(tmp_path))
    original = json.loads(manager.path(REF).read_text())['cookies']
    with pytest.raises(sessions.SessionError):
        manager.token(REF)
    saved = json.loads(manager.path(REF).read_text())
    assert saved['cookies'] == original
    assert saved['failures'] == 1
    assert manager.token(REF, refresh=False) == token()
