# Binance USD-M actual daily funding

`src/services/binance_funding.py` exposes:

```python
await fetch_binance_funding(store, refs, start, end, request_json, api_base)
# -> dict[str, Decimal], base instrument -> signed nominal USD amount
```

The helper is wired into `FundingReportService` and the dependency factory for
enabled profiles. A venue failure discards its rows and marks the report
incomplete while retaining other venues. Report wiring does not enable profiles,
place orders, or authorize delivery. The configured disabled Binance profile must
remain disabled unless the operator separately authorizes activation.

## Vendor contract and sources

Researched against the current official catalog:

- [Get Income History / Account REST API](https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/rest-api/account#get-income-history)
- [General information and signing](https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info)
- [Binance-maintained legacy Python connector, `get_income_history`](https://github.com/binance/binance-futures-connector-python/blob/main/binance/um_futures/account.py)

The old `.../account/rest-api/Get-Income-History` URL redirects to the catalog;
the old `/en/docs/derivatives/...` URL was unavailable during research. Use the
catalog for endpoint parameters, not a guessed Binance-compatible route.

Confirmed contract:

- Production `GET https://fapi.binance.com/fapi/v1/income`, `USER_DATA`, IP weight 30.
- `X-MBX-APIKEY` header, timestamp in milliseconds, `recvWindow`, HMAC-SHA256
  signature of the exact query bytes preceding the final `signature` parameter.
  Both API key and secret are case-sensitive. This helper supports HMAC secrets,
  not RSA/Ed25519 key material.
- `incomeType=FUNDING_FEE`; no `symbol` filter (includes closed contracts).
- `startTime` and `endTime` are inclusive millisecond bounds. The helper accepts
  aware, millisecond-aligned `[start, end)` and sends `endTime=end_ms-1`.
- `page` **is documented**, and `limit` defaults to 100, maximum 1000. The helper
  explicitly sends `page=1`, `limit=1000` on every queried interval.
- Rows carry `tranId`, `symbol`, `incomeType`, string `income`, `asset`, and integer
  millisecond `time`. The legacy vendor SDK documents transaction IDs as unique
  within an income type for a user. The catalog describes `tranId` as int64;
  canonical positive decimal-string IDs are also accepted and deduplicated with
  the equivalent integer ID. Booleans, floats, signs, whitespace, and malformed
  identifiers are rejected.
- `income` is used directly, preserving positive receipts and negative charges.
  It is not a funding rate, annualized estimate, position snapshot, or sign flip.

## Completeness strategy

The catalog establishes `page` but does not spell out a stable multi-page snapshot
or all ordering/tie semantics. Rather than assume advancing timestamps or page
order is safe, the helper bisects **capped time intervals**:

1. Query the full requested interval with page 1 and limit 1000.
2. If fewer than 1000 rows arrive, that interval is an uncapped leaf.
3. For exactly 1000 rows, validate the rows but do not count the parent. Split
   `[low, high)` into `[low, middle)` and `[middle, high)`, then query both.
4. Count only uncapped leaves. Identical IDs are counted once per API-key scope;
   conflicting records anywhere, including between capped parents and leaves,
   fail closed.
5. A capped single millisecond cannot establish completeness: raise, not truncate.
   Exhausting the global 256-request budget also raises, even after prior success.

This assumes the documented limit returns up to the requested number of matching
rows, without an undocumented smaller server cap. No REST traversal can prove
snapshot consistency during historical ledger mutation; callers should select a
completed window and may reconcile later vendor corrections independently.

There is a 0.51-second pause between requests across intervals and profiles, with a
fresh timestamp/signature generated **after** each pause. This is not a shared
IP-wide rate limiter; concurrent collectors can still consume the venue's weight
budget. HTTP 429/418, transport errors, malformed payloads, and business-error
objects fail the entire fetch, without returning partial totals or retry storms.
The injected transport must reject non-success HTTP status codes; redirects are
explicitly disabled. Errors exposed by the helper are sanitized.

### Retention limitation

The current retrieved catalog does not specify a total retention horizon. The
vendor's legacy SDK says omitting both time bounds returns the recent seven days;
that is a **default query window**, not proof of full retention or permission to
backfill arbitrarily old dates. This helper always supplies both bounds and is
intended for the latest completed daily window. It cannot detect a silently empty
out-of-retention response or certify archive coverage. Do not present arbitrary
historical backfills as complete without separately proving vendor retention.

## Credentials, settlement, and exact accounting

- Credentials are read only via `store.get_exchange_credentials(ref)` for each
  supplied ref. No settings, environment fallback, default profile, or inherited
  connector signer is consulted. All profiles are validated before the first
  request. Duplicate API-key aliases share one fetch; inconsistent alias secrets
  raise before any network call. Different API keys are independent scopes even
  if their ledger transaction IDs match.
- This is API-key alias dedupe, not account discovery: two different keys for the
  same underlying account cannot be recognized as aliases without another proven
  account-identity contract. Do not configure both as independent report accounts.
- Only the exact HTTPS production origin (optionally one trailing slash) is
  allowed. Userinfo, paths, query strings, alternate ports, testnet, and COIN-M
  origins are rejected before credentials can be transmitted.
- Only **USDT and USDC** settlement assets are accepted, at nominal 1 USD per unit.
  This is not an FX/depeg valuation. Unsupported assets fail the fetch rather than
  inventing USD values for coin collateral. The symbol must end in the exact
  settlement suffix; remove that suffix once (`NCCOXAG2USDUSDT` -> `NCCOXAG2USD`).
- Strict finite decimal strings are parsed without floats and summed with a
  precision-sized local Decimal context. Long amounts and cancellation remain
  exact even if the caller's Decimal precision is low. Rounding is left to display.
- A complete empty history returns `{}`, not invented per-market zeros. Any later
  profile/page failure raises and makes previously accumulated totals unavailable.

## Verification

Tests cover signed **actual httpx wire-query bytes**, inclusive vendor boundaries,
closed-symbol income, both settlement assets, API-key aliases, account-scoped IDs,
conflicts, schema/business errors, hostile origins, missing credentials, exact
low-precision money, unsorted capped intervals, saturation, request budgets, later
failures, and signing after pacing.

```sh
python -m pytest tests/test_binance_funding.py -q
ruff check src/services/binance_funding.py tests/test_binance_funding.py
```

All fixtures use synthetic credentials. No secrets were read and no live calls were
made by this implementation task. The parent independently reported a signed
HTTP 200 `[]` for 2026-10-15 11:30 through 2026-10-16 11:30 Europe/Moscow,
with the profile still disabled. That verifies access and an empty recent window,
not live nonzero accounting, capped-interval behavior, or older retention.
