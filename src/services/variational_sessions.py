"""Profile-bound Omni cookie renewal. No writes to the credential store.

JWT claims are identity/expiry guards, not local signature verification; only
trusted HTTPS Set-Cookie responses can renew an imported session.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager, nullcontext
import fcntl
from http.cookiejar import Cookie, CookieJar
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time

import httpx

from src.services.credential_store import CredentialStore

ORIGIN = 'https://omni.variational.io'
ERROR = 'Variational session unavailable'


class SessionError(ValueError):
    """Sanitized: never attach request/response/cookie details."""


def _claims(token):
    try:
        parts = token.split('.')
        if len(parts) != 3 or not all(parts):
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(parts[1] + '=' * (-len(parts[1]) % 4)))
        address = data['address'].lower()
        company = data.get('company') or data.get('company_id')
        if not re.fullmatch(r'0x[0-9a-f]{40}', address) or not isinstance(company, str) or not company:
            raise ValueError
        if any(type(data.get(k)) is not int for k in ('iat', 'exp')) or data['exp'] <= data['iat']:
            raise ValueError
        return {'address': address, 'company': company}, data['iat'], data['exp']
    except Exception:
        raise SessionError(ERROR) from None


def _read(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise SessionError(ERROR)
        return json.load(stream)


def _atomic(path, data):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.session-')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, separators=(',', ':'))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _jar(rows, identity):
    jar = CookieJar()
    allowed = {'vr-token', 'vr-connected-address', 'vr-ll-token',
               'vr-ll-token-' + identity['address'], '__cf_bm', 'cf_clearance', '_cfuvid'}
    for raw in rows:
        name = raw.get('name', '')
        if name not in allowed:
            continue
        domain = raw.get('domain', '')
        if domain not in {'omni.variational.io', '.omni.variational.io', '.variational.io', 'variational.io'}:
            raise SessionError(ERROR)
        if not name.startswith(('__cf', 'cf_', '_cf')) and domain.lstrip('.') != 'omni.variational.io':
            raise SessionError(ERROR)
        if not raw.get('secure') or not str(raw.get('path', '')).startswith('/'):
            raise SessionError(ERROR)
        args = dict(raw)
        args['rest'] = args.pop('_rest', args.pop('rest', {}))
        jar.set_cookie(Cookie(**args))
    names = {c.name for c in jar}
    if 'vr-token' not in names:
        raise SessionError(ERROR)
    addresses = [(c.value or '').lower() for c in jar if c.name == 'vr-connected-address']
    if addresses != [identity['address']]:
        raise SessionError(ERROR)
    return jar


def _failure_cookies(jar, original, identity):
    """Keep scoped refresh/CF changes, never an unvalidated access identity.

    A positively foreign identity taints the whole response. Malformed access
    tokens cannot be published, but do not discard independent refresh rotation.
    HTTPX has already applied domain/path matching and deletion semantics.
    """
    rows = [vars(c) for c in jar]
    for row in rows:
        if row['name'] == 'vr-connected-address' and (row['value'] or '').lower() != identity['address']:
            raise SessionError(ERROR)
        if row['name'] == 'vr-token':
            try:
                candidate, _, _ = _claims(row['value'])
            except SessionError:
                continue
            if candidate != identity:
                raise SessionError(ERROR)
    protected = {'vr-token', 'vr-connected-address'}
    retained = [row for row in original if row['name'] in protected]
    previous = {(row['domain'], row['path'], row['name']): row for row in original}
    for row in rows:
        if row['name'] in protected:
            continue
        try:
            # Reuse the import allowlist and HTTPS/domain/path guards. Unknown
            # names are filtered out; invalid replacements retain the old value.
            checked = _jar(retained + [row], identity)
        except SessionError:
            old = previous.get((row['domain'], row['path'], row['name']))
            if old is not None:
                retained.append(old)
        else:
            retained = [vars(c) for c in checked]
    return retained


def _token(jar):
    matches = [c.value for c in jar if c.name == 'vr-token' and c.path == '/']
    if len(matches) != 1:
        raise SessionError(ERROR)
    return matches[0]


class VariationalSessions:
    def __init__(self, store: CredentialStore, *, origin=ORIGIN, clock=time.time, transport=None):
        if origin != ORIGIN:
            raise SessionError(ERROR)
        self.store, self.clock, self.transport = store, clock, transport
        self.root = store.storage_path.parent / 'variational-sessions'

    def path(self, ref):
        if not re.fullmatch(r'variational(?::[a-z0-9][a-z0-9_-]{0,63})?', ref):
            raise SessionError(ERROR)
        return self.root / (ref.replace(':', '--') + '.json')

    @contextmanager
    def _lock(self, ref):
        path = self.path(ref)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise SessionError(ERROR)
        os.chmod(self.root, 0o700)
        fd = os.open(path.with_suffix('.lock'), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield path
        finally:
            os.close(fd)

    def import_file(self, ref, source: Path):
        try:
            with self._lock(ref) as path:
                identity, _, _ = _claims(self.store.get_exchange_credentials(ref).get('vr_token', ''))
                source_data = _read(source)
                jar = _jar(source_data['cookies'], identity)
                candidate_identity, _, expiry = _claims(_token(jar))
                if candidate_identity != identity or expiry <= self.clock():
                    raise SessionError(ERROR)
                names = {c.name for c in jar}
                if not {'vr-ll-token', 'vr-ll-token-' + identity['address']} <= names:
                    raise SessionError(ERROR)
                addresses = [c.value.lower() for c in jar if c.name == 'vr-connected-address']
                if addresses != [identity['address']]:
                    raise SessionError(ERROR)
                ua = next((v for k, v in source_data.get('headers', {}).items()
                           if k.lower() == 'user-agent'), 'Mozilla/5.0')
                if not isinstance(ua, str) or '\n' in ua or '\r' in ua:
                    raise SessionError(ERROR)
                _atomic(path, dict(version=1, profile=ref, identity=identity, user_agent=ua,
                                   cookies=[vars(c) for c in jar], failures=0, retry_at=0))
        except Exception:
            raise SessionError(ERROR) from None

    def token(self, ref, *, refresh=True, fallback=None):
        """No jar: preserve static behavior. Existing invalid jar: fail closed.

        One request per attempt, persisted capped exponential backoff, no hidden
        in-request retry. Cross-process flock includes disk reread and network.
        """
        try:
            path = self.path(ref)
            static = self.store.get_exchange_credentials(ref).get('vr_token') or fallback or ''
            if not os.path.lexists(path):
                return static
            # Atomic replace makes a lock-free read safe for synchronous alert
            # collection, which must not wait behind a refresh network timeout.
            with self._lock(ref) if refresh else nullcontext():
                state = _read(path)
                identity, _, _ = _claims(static)
                if state['version'] != 1 or state['profile'] != ref or state['identity'] != identity:
                    raise SessionError(ERROR)
                jar = _jar(state['cookies'], identity)
                old = _token(jar)
                old_identity, old_iat, old_exp = _claims(old)
                if old_identity != identity:
                    raise SessionError(ERROR)
                if not refresh or not self.store.is_exchange_enabled(ref) or old_exp - self.clock() > 150:
                    return old
                if state.get('retry_at', 0) > self.clock():
                    raise SessionError(ERROR)
                try:
                    # Import lazily to avoid the connector/funding dependency cycle.
                    from src.connectors.real_connectors import VariationalRealConnector
                    headers = VariationalRealConnector._default_headers(old)
                    headers.pop('Cookie', None)  # never shadow the live jar
                    headers['User-Agent'] = state['user_agent']
                    headers['Content-Type'] = 'application/json'
                    with httpx.Client(cookies=jar, headers=headers, timeout=15,
                                      follow_redirects=False, transport=self.transport, trust_env=False) as client:
                        response = client.post(ORIGIN + '/api/auth/refresh')
                        state['cookies'] = _failure_cookies(client.cookies.jar, state['cookies'], identity)
                        if response.status_code != 200:
                            raise SessionError(ERROR)
                        response.json()  # reject challenge/HTML, never log the body
                        renewed = _jar([vars(c) for c in client.cookies.jar], identity)
                        new = _token(renewed)
                        new_identity, new_iat, new_exp = _claims(new)
                        if (new_identity != identity or new_iat <= old_iat or new_exp <= old_exp
                                or new_exp <= self.clock() + 150 or new_iat > self.clock() + 30):
                            raise SessionError(ERROR)
                    _atomic(path, dict(state, cookies=[vars(c) for c in renewed], failures=0, retry_at=0))
                    return new
                except Exception:
                    failures = min(int(state.get('failures', 0)) + 1, 6)
                    state.update(failures=failures, retry_at=self.clock() + min(30 * 2 ** (failures - 1), 900))
                    _atomic(path, state)
                    raise SessionError(ERROR) from None
        except Exception:
            raise SessionError(ERROR) from None
