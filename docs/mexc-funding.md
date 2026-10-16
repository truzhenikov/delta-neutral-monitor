# MEXC actual historical funding

## Supported interface

`await fetch_mexc_funding(store, refs, start, end, request_json, api_base) -> dict[str, Decimal]`

Module: `src/services/mexc_funding.py`. Pass explicit profile references and the existing
credential store. `api_base` must be exactly `https://contract.mexc.com` (not the
existing account connector's `https://api.mexc.com` default). No environment/default
credential fallback is used. Account aliases sharing one API key are fetched once;
conflicting secrets for the same key are rejected before any request.

`request_json` must reject unsuccessful HTTP status and decode numeric JSON with
`response.json(parse_float=Decimal)`. Binary floats are deliberately rejected.
Caller must preserve supplied sorted params and `follow_redirects=False`; do not
reorder the signed query. Only authenticated GET is used.

Returns base-symbol → actual signed USDT funding, nominal USD. Positive means
received, negative means paid. It never uses a rate × current position estimate.
Only `*_USDT` settlement is supported; other contracts (including USDC, until
separately evidenced) and contradictory currency fields fail the entire result.
No current-position filter is used, so closed markets remain eligible.
A complete empty history returns `{}`, not synthetic per-symbol zero rows.
A failed profile/page, malformed record, ambiguous pagination, request exhaustion,
or API rejection raises a sanitized `ValueError`; no partial result escapes.

## Official evidence

- Current ledger: https://www.mexc.com/api-docs/futures/account-and-trading-endpoints/get-funding-fee-details
  specifies GET `/api/v1/private/position/funding_records`, permission **View Order
  Details**, limit **20 requests / 2 seconds**, optional symbol/position filters,
  `page_num`, `page_size` (maximum 100), and millisecond `start_time` / `end_time`.
  Response fields include actual `funding`, `settleTime`, `id`, `symbol`, plus
  `pageSize`, `currentPage`, `totalCount`, `totalPage`, `resultList`.
  Its displayed JSON example incorrectly shows a public funding-rate object;
  implementation follows the documented ledger fields, confirmed live.
- Current auth: https://mexc.com/api-docs/futures/integration-guide specifies
  dictionary-sorted GET business parameters and HMAC-SHA256 over
  `accessKey + timestamp + encodedParameterString`; send `ApiKey`, `Request-Time`,
  `Signature`. `Recv-Window` is in seconds, maximum 60; this helper sends `10`.
- Official legacy complete contract documentation:
  https://mexcdevelop.github.io/apidocs/contract_v1_en/ establishes the production
  origin `https://contract.mexc.com`, ledger response/pagination examples, and
  examples where a long with negative rate receives positive `funding`.
  Position `fundingFee` description explicitly says positive received, negative
  lost. Signed ledger `funding` is retained unchanged, also consistent with
  CCXT's implementation (supporting evidence, not the authority):
  https://github.com/ccxt/ccxt/blob/master/python/ccxt/mexc.py (`fetch_funding_history`).
- Public GET `/api/v1/contract/detail` live confirmation for BTC_USDT:
  `baseCoin=BTC`, `quoteCoin=USDT`, `settleCoin=USDT`. Unsupported settlement
  cannot silently be counted as USD. No dependency on currently listed contracts
  is introduced, avoiding exclusion of delisted/closed-market ledger records.

## Completeness and precision

The query includes a conservative inclusive millisecond envelope. Attribution uses
exact UTC integer-microsecond boundaries converted without ambient Decimal
rounding, then compares at the ledger's full numeric precision using `[start,end)`.
Every page must agree on count, page count, requested page size, and current page.
Every page must have exactly its count-implied length; collected unique IDs must
match totalCount. Repeated IDs, changing totals and truncated/saturated ambiguous
responses fail closed. Tied timestamps are harmless because pagination uses page
numbers, not timestamp advancement. A full terminal page is accepted only when
its authoritative metadata proves completion. Request budget: 256 across all
profiles. Requests after the first wait 0.12 seconds, below the documented cap;
a fresh timestamp/signature is generated after the wait. Financial additions use
an explicitly sufficient local Decimal precision even under low ambient context.

### Retention qualification

Neither the current funding-ledger page nor its legacy equivalent specifies a
retention horizon. Do **not** borrow the 90-day query-span restriction from other
MEXC order endpoints or claim unlimited history. Live unfiltered traversal found
491 retained records from **2026-07-29 00:00 UTC** through **2026-10-16 00:00 UTC**.
That is observed account coverage, not a promised retention SLA and not proof
that July 29 is the first-ever payment. This integration reports the exchange's
returned ledger; arbitrary archival windows outside observed coverage need
separate retention verification. The exact latest completed daily window below
is inside the observed range, and was independently verified by fetching the
entire unfiltered retained ledger, not merely trusting time-query acceptance.

## Sanitized live verification (2026-10-16 UTC)

Existing active profile: `mexc:mexc`, read only via `src.deps.get_credential_store()`.
No credential writes, orders, transfers, restarts, or notifications.

Exact latest completed window:

- UTC: **[2026-10-14 08:30:00, 2026-10-15 08:30:00)**.
- Europe/Moscow: **[2026-10-14 11:30:00, 2026-10-15 11:30:00)**.
- Epoch milliseconds: **[1791966600000, 1792053000000)**.

Authenticated account-assets GET: HTTP 200, `success=true`, `code=0`.
Authenticated filtered funding GET: HTTP 200, `success=true`, `code=0`,
`pageSize=100`, `currentPage=1`, `totalCount=0`, `totalPage=0`, zero rows.
Production helper returned **`{}`** for that exact window.

Independent aggregation used separately implemented HMAC transport and fetched
all unfiltered account ledger pages, decoding decimals before arithmetic. Page
lengths were **100, 100, 100, 100, 91**, stable `totalCount=491`, `totalPage=5`;
all 491 IDs were distinct. Filtering locally to the exact same window matched
**0 records**, with independent signed symbol totals **`{}`**. This confirms a
complete empty result, not live nonzero accounting. No keys, secrets, signatures,
raw record IDs, account balances, or signed URLs are retained here.

## Tests

Strict initial RED: all 55 focused tests failed while the module was absent;
then GREEN: 55 passed. A separate RED→GREEN corrected the contract-specific
`Recv-Window` units (`5000` → `10`). An additional raw JSON mock-transport test
covers exact fractional monetary/timestamp decoding just below the end cutoff.
Run `python3 -m pytest tests/test_mexc_funding.py -q` and the full suite.
Shared report/dependency/configuration wiring is intentionally outside this
module's scope; the parent integration must supply the contract origin and
Decimal-aware response decoding.
