# Backpack historical funding ledger

## Evidence and exact contract

Primary source: [official OpenAPI rendered at docs.backpack.exchange](https://docs.backpack.exchange/#tag/Position/operation/get_funding_payments), specifically `paths["/wapi/v1/history/funding"].get` and `components.schemas.FundingPayment`. The embedded `__redoc_state.spec.data` is the actual OpenAPI, not an inferred frontend route.

- Production origin: `https://api.backpack.exchange`.
- Only `GET /wapi/v1/history/funding`; signing instruction `fundingHistoryQueryAll`.
- Optional query `symbol` (omitted means **all markets**), `limit` (default 100, maximum 1000, uint64), `offset` (default 0, uint64), `sortDirection` (`Asc` or `Desc`). **No start/end filter and no cursor.**
- Successful response is a JSON **array**. Documented response headers include `X-PAGE-COUNT`, `X-CURRENT-PAGE`, `X-PAGE-SIZE`, and `X-TOTAL`; this transport consumes JSON only and does not depend on those headers.
- Required fields: `userId` (int32), `symbol` (string), `quantity` (decimal string), `intervalEndTimestamp` (string, **naive-date-time**), `fundingRate` (decimal string). Optional `subaccountId` is uint16.
- `quantity` description verbatim: **“Quantity of the payment. Positive if received, negative if paid.”** Preserve this sign; never multiply by position direction or funding rate.
- `intervalEndTimestamp` description: “The end of the funding interval for the payment.” This is a date-time string, **not** numeric milliseconds/microseconds. The authentication header timestamp is separately Unix **milliseconds**. The websocket's microsecond timestamp rules do not apply to this REST field.
- No payment/transaction ID is specified by `FundingPayment`. Do not require one or convert `userId` through float. Optional future `id` values are checked as nonnegative exact integers or decimal-digit strings without float conversion; they are not used to invent uniqueness.

[Official futures specifications](https://support.backpack.exchange/technical-docs/trading/futures-specs) state that markets are denominated and settled in USDC. This implementation deliberately accepts only `<BASE>_USDC_PERP`, rejects contradictory explicit asset/currency fields, and reports **nominal USDC**, not a guaranteed USD exchange rate. It makes no balance/equity assumption.

The [official SDK](https://github.com/backpack-exchange/bpx-api-client/tree/addd51e1feaf969a5f196f487ff38b0320013c47) has public funding interval rate types (`types/src/markets.rs`, `FundingRate`) with `interval_end_timestamp: String`; it does not supply a stronger historical-payment/timezone contract than OpenAPI. A public GET to `/api/v1/fundingRates?symbol=BTC_USDC_PERP&limit=1` returned a naive `2026-10-20T21:00:00` shape during development.

The parent/operator independently verified authenticated, read-only history retrieval with a staged candidate (no runtime profile): six pages containing **1000, 1000, 1000, 1000, 743, 0** payments, totaling **4743 historical records**, with the first timestamp `2025-01-23T16:00:00`. Actual fields were `fundingRate`, `intervalEndTimestamp`, `quantity`, `symbol` as strings and `subaccountId`, `userId` as integers. All records passed ledger validation. For **19 October 2026 11:30–20 October 2026 11:30 MSK**, the complete result was `{}`. This proves live historical record shape and traversal, **not nonzero daily-window accounting or the timezone interpretation**. No credentials or account identifiers are retained here.

### Timestamp qualification

The parser interprets the exchange's timezone-less REST date-time as **UTC**, also accepting explicit `Z`/offset representations, and retains up to nine fractional digits with integer arithmetic. OpenAPI calls the field `naive-date-time` but does **not explicitly spell out its timezone**. UTC interpretation is an integration assumption, not independently established by a private live nonzero fixture. Before enabling production reporting, confirm a sanitized actual payment against its UTC funding interval/UI. Test fixtures are schema-based synthetic raw JSON, not claimed live transactions.

## Completeness and failure behavior

- Caller must pass **every enabled Backpack reference**; this module preflights all supplied credentials before any IO. It uses the connector's profile-explicit `validate_keypair` to check a matching base64 Ed25519 public key and base64 32-byte seed, without environment/default-profile fallback.
- Duplicate public-key references are queried once only if the **entire credential dictionary agrees**, including optional passphrase/other secrets. Conflicts or later missing credentials fail before requests.
- Fetch every page in explicit ascending order, without a current-position or market filter, so closed positions are included. Offset advances by **actual rows returned**. Even short/capped pages require a following empty page; a full page never implies completion.
- Identity is `(userId, subaccountId, symbol, intervalEndTimestamp)`, with timestamps normalized at nanosecond precision. Within one key, duplicate identities (even changed quantities) fail closed as pagination overlap. Across distinct keys, identical events for the same underlying user/subaccount count only once; conflicting quantity or funding rate fails the whole venue closed. Separate users/subaccounts remain distinct. Since no transaction ID is documented, indistinguishable legitimate same-interval records within one key fail closed too; this is safer than under/overcounting. A repeated/ignored offset cannot produce an apparently complete ledger.
- No history retention horizon or snapshot-isolation guarantee is documented here. Ascending traversal reduces interference from new tail records but is not a transactional snapshot proof. Pruning/reordering of history during pagination cannot be ruled out without stronger exchange guarantees. All available pages are traversed; exhaustion of the budget/deadline yields unavailable, never reconstructed/zero history.
- Per-request timeout 15 seconds, whole-call timeout 120 seconds, at most 256 requests **across all profiles**, 0.1-second spacing, fixed HTTPS origin, redirects disabled, GET only. Transport must honor kwargs and raise on non-success HTTP statuses.
- Any invalid page/record/asset, repeated identity, timeout or later profile/page failure raises. Locally accumulated values never escape on failure. API error JSON is never an empty successful ledger. A genuinely complete empty response returns `{}`, not invented symbol zeros.
- Decimal coefficients are accumulated as integers and reconstructed directly, remaining exact even under reduced ambient Decimal precision. Amount/rate inputs must be finite fixed-point decimal strings, bounded to 1024 characters.

## Shared report integration

The available API is:

```python
await fetch_backpack_funding(store, enabled_backpack_refs, start, end, request_json, backpack_api_base)
# -> dict[str, Decimal], e.g. {"BTC": Decimal("-0.25")}
```

1. The report builder imports `fetch_backpack_funding` and passes only `_enabled_refs('backpack')`, `backpack_api_base` (official origin by default), and its shared request transport. With no enabled Backpack references, it never invokes the collector, reads Backpack credentials or sends Backpack requests.
2. Reuse `FundingReportService.completed_window(now)` for the latest completed **11:30–11:30 Europe/Moscow** window (08:30–08:30 UTC). This module enforces exact half-open `[start,end)` at nanosecond precision, including the start and excluding the end. It does not change scheduling or select its own clock-dependent reporting window.
3. Venue totals enter the report only after this await fully succeeds. On exception, a sanitized Backpack warning marks the report incomplete and no Backpack rows are retained; unrelated venues remain. No current snapshots, rates, balance deltas, zeros or partial pages are substituted.
4. Dependency wiring forwards configured `backpack_api_base`, using the official default when older settings doubles lack the field. The existing configuration field is reused; no runtime settings or profiles are changed.
5. The signer is **already reused** from `BackpackRealConnector._headers`, with explicit profile-bound public key/private key and identical alphabetically encoded wire query. If refactoring later, move `validate_keypair` and `_headers` together into a shared auth helper; do not add a second signer or environment credential lookup.
6. **Keep Backpack disabled.** Code/tests do not authorize activation, credential persistence, deployment, scheduled delivery or further live private probes. This shared-wiring/review task accessed no runtime credential files and performed no live exchange calls; the independently reported parent probe is qualified above.

## Verification

`python3 -m pytest tests/test_backpack_funding.py tests/test_backpack_funding_integration.py -q` covers raw-JSON timestamp boundaries, exact large signed quantities under precision 3, Ed25519 verification of actual URL query, full/capped pages, cross-key same-account deduplication/conflicts, credential alias conflicts/all-profile preflight, repeated offsets, later failures, malformed IDs/NaN/assets, exact huge optional IDs and origin validation. Integration tests exercise the actual ledger through the report and renderer, window/reference/base forwarding, disabled/no-access/no-mutation behavior, later-page/profile failure isolation and secret redaction, empty history and dependency compatibility. The full suite and scoped Ruff are separate regression gates.
