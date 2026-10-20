# Variational cookie-only session renewal

## Scope and invariants

No wallet private key or login signature is needed. This implementation renews an
**already authenticated imported cookie session**, not a revoked/expired long-lived
login. The server can require a new browser login at any time. One profile's proven
renewal does not establish that another profile's cookies work.

Each exact reference (`variational:main`, etc.) has its own file under
`Path(CREDENTIAL_STORE_PATH).parent / 'variational-sessions'`. For the default
`data/credentials/exchanges.json`, that is `data/credentials/variational-sessions/`.
The directory is 0700; cookie files, temporary files and stable lock files are
0600. No new credential-store fields and **no credential-store writes** are used.
Old bot/store writers cannot erase the renewed cookies. Backup systems must treat
this entire directory as credentials, not ordinary application state.

Backend snapshots and funding histories lazily consult the same manager. The
factory already sets the exact connector profile; no constructor/factory signature
change is needed. No jar means the existing static `vr_token`/connector environment
fallback behavior remains unchanged. A present but malformed/mismatched jar fails
closed rather than silently using an old token. Disabling a profile prevents
renewal; importing never enables it. Existing profile selection remains unchanged.

## Local import (operator action; not part of source installation)

Prepare a protected local JSON capture, owned by the same OS user as the services,
with permissions 0600. Never put cookie values in command-line arguments, chat,
logs or git. The accepted capture shape is:

```text
{headers: {"User-Agent": ...}, cookies: [vars(http.cookiejar.Cookie), ...],
 original_exp: ..., response_body: "<JSON response string>"}
```

Only `cookies` and the User-Agent header are consumed. Copied Cookie/Authorization
headers, response bodies and the original expiration are **not** persisted.
Import compares the capture JWT address and company/company_id to the current
`vr_token` in the explicit destination profile. It requires a still-live token,
matching `vr-connected-address`, generic `vr-ll-token`, and that wallet's
`vr-ll-token-<lowercase-address>`. Foreign account cookies are excluded, never merged.
Only active-account cookies on Omni and relevant CF cookies on Variational domains
are retained, with their original scopes. Cookie records must be secure HTTPS
cookies. The source cannot be a symlink or a group/world-readable file.

From the repository directory, using the service's configured credential path:

```bash
.venv/bin/python -m src.scripts.variational_sessions import \
  --profile variational:main --source /secure/bootstrap.json \
  --credential-store data/credentials/exchanges.json
.venv/bin/python -m src.scripts.variational_sessions once \
  --credential-store data/credentials/exchanges.json
```

The importer performs no network calls and prints no secrets. It may import into a
disabled profile without enabling or renewing it. It replaces only that profile's
jar under the same lock used for renewal. Keep the source protected until rollout
verification is complete, then remove it according to local secret-retention policy.
Do not inspect jars with `cat` or paste their content into diagnostics.

## Renewal and supervision

A read checks JWT expiry, renewing when at most 150 seconds remain. Refresh uses
only `POST https://omni.variational.io/api/auth/refresh`, no body, JSON content type,
profile wallet header, browser User-Agent, Origin and Referer. The HTTP client has
an explicit 15-second timeout, rejects redirects, ignores ambient proxy/netrc
configuration, and uses the cookie jar rather than a static Cookie header.

A stable per-profile `flock` covers disk reread, request, validation and atomic
fsync/replace. Independent backend, bot, history and keeper processes cannot renew
the same session simultaneously. A second waiter reloads the first one's new JWT
and does not refresh again while it is fresh. Cookies are processed by the HTTP
client with Set-Cookie domain/path/expiry/deletion semantics. Active account
identity must remain unchanged; `iat` and `exp` must both advance, with more than
150 seconds remaining, before the new token is published. Claims are guards, not
local JWT signature verification: renewal is trusted only from the fixed TLS
origin. No arbitrary host, redirect, primary key or signing endpoint is supported.

Failures are sanitized and do not publish a candidate token. After an HTTP response,
matching long-lived (`vr-ll-token` and the active wallet's scoped variant) and CF
cookie rotations/deletions are atomically retained even on non-200 status, malformed
JSON or rejected access JWT, so restart/retry uses the latest refresh cookies.
The previously validated `vr-token` and `vr-connected-address` remain unchanged on
failure. HTTPX applies domain/path/deletion semantics; retained values must pass
the same cookie-name, domain, path and secure-HTTPS allowlist as import. An insecure
or otherwise invalid replacement is ignored without erasing a valid old value.
A response that identifies a foreign wallet/company, including an alternate-path
access cookie, is rejected in full: none of its cookie changes are merged into the
profile. A mismatched on-disk profile fails closed before any request or write.
One attempt is made per eligible call; persisted exponential retry delays are 30, 60, 120, 240, 480,
then at most 900 seconds, shared across processes and restarts. A failed refresh
cannot spin on every collector poll. Invalid/revoked cookies eventually require a
fresh browser capture; no automated reauthentication fallback is attempted.

`keep` performs the same check immediately, then every 60 seconds, independently
of account/history demand. It scans only enabled configured Variational profiles
with imported session files. SIGTERM/SIGINT shut it down cleanly. The supervised
unit template is `deploy/delta-neutral-monitor-variational-sessions.service`.
Install/enable it only as an explicitly authorized deployment action, after import.
The template uses the repository virtualenv and restricts writes to the default
session directory. Override `ReadWritePaths` if the credential-store location is
custom; use the same service user/environment as the backend and bot.

Expiry alerts read the atomic live session snapshot **without taking the network
refresh lock**. Managed sessions warn only within 30 seconds, leaving normal
renewal at 150 seconds and the 60-second keeper poll a grace window without repeated
warnings on healthy five-minute rotations, and still report expiration. Static profiles
retain the existing 24h policy. Invalid managed files produce a sanitized sticky
`TOKEN SESSION UNAVAILABLE` alert. The keeper logs only profile-level failure,
never responses or cookies. Snapshots also mask Variational transport failures.

## Rollout acceptance (must be exercised separately)

1. Deploy source/virtualenv consistently to backend, bot and history consumers;
   restart those consumers so none continue reading the old static JWT.
2. Import only the explicitly authorized profile. Do not clone its jar to other
   profiles; other accounts keep their existing static behavior until separately
   authorized captures are supplied. Confirm disabled flags are unchanged.
3. Run `once`, then start supervision. A fresh jar with more than 150 seconds left
   deliberately does not call refresh yet; success from `once` alone is not proof
   a refresh occurred.
4. Observe advancing expiry across several renewal cycles, cross the original and
   first-renewed expiration, and require portfolio/positions success for the exact
   profile. Run the actual funding-history builder without sending a report.
5. Restart the keeper and confirm the next cycle renews from the persisted jar.
   Verify the unit is active/enabled with stable restart count and no auth loop.
6. Verify expiry alerts use the renewed expiry, profile health is fresh, and other
   connectors remain healthy. Aggregate health may be degraded independently.

No deployment, real cookie import or live account verification is performed by the
unit tests. Synthetic coverage includes actual two-process locking, restart reload,
rotations/deletions/scopes, identity/origin rejection, persisted backoff, protected
permissions, profile isolation, static fallback and all three token-consumer paths.

## Recovery

Stop a failing keeper before investigation. Do not dump its environment or JSON
files. A new local browser capture imported into the same profile atomically resets
backoff and replaces that jar. Removing a jar explicitly restores static-token
behavior, **which may immediately fail if the old credential-store JWT is expired**;
this is not a transparent rollback. Never overwrite another profile to hide an
account failure. Credential-store deletion or account-identity changes make an
existing jar unusable until explicitly rebound through a matching fresh import.
