# Polymarket PERPS historical funding

## Scope and verification status

The daily funding report reads **account-wide realized funding history**, including
payments on positions that are now closed. It does not estimate funding from
current positions, rates, position-cycle snapshots, or cumulative portfolio fields.
Only explicitly enabled `polymarket` profile references are collected. Disabled
profiles cause no collector call, credential read, public metadata request, or
credential-store mutation. Adding this code does not enable any profile.

**Sign verification now includes sanitized records supplied from an authenticated
live read for 23 October 2026 11:30 through 24 October 2026 11:30 MSK.** The
instrument-50 CXMT long-size-800 records below establish the API's signed-cashflow
convention. Regression tests replay their exact financial fields offline using
invented account identities and credentials; they perform no authenticated live
requests. This fix did not create or rotate credentials or activate profiles.
The supplied live read reported 24 payments with complete (`more: false`)
responses, totaling +6.206798 Polymarket funding; Hyperliquid signed funding was
+0.703104, giving +6.909902 combined. The aggregate regression is explicitly a
synthetic aggregate, not a reconstruction of all 24 unavailable raw records.
This is live API evidence, **not independent UI/balance-change reconciliation**.

## Authoritative sources inspected

- [Account management: funding payments](https://docs.polymarket.com/perps/account-management#funding-payments)
  documents the account-wide REST history and unique payment IDs.
- [Authenticated sessions](https://docs.polymarket.com/perps/authenticated-sessions)
  documents existing proxy credentials for private REST reads. The proxy private
  key signs trading operations; it is **not needed** for these reads. Initial
  registration/credential issuance is a separate, signing-capable operator workflow
  and is intentionally not implemented by this collector.
- [Official REST OpenAPI](https://docs.polymarket.com/api-spec/perps-openapi.json):
  `/v1/account/funding`, `/v1/account/credentials`, `AccountFundingData`, `fund`,
  `fid`, `ts`, `start_timestamp`, and `end_timestamp`.
- [Funding mechanism](https://docs.polymarket.com/perps/learn-about-trading/funding):
  positive hourly rates mean longs pay and shorts receive; settlement debits or
  credits quote balance separately from trading PnL.
- [Polymarket USD](https://docs.polymarket.com/concepts/pusd): pUSD is a USDC-backed
  collateral wrapper. This is not evidence of a real-time fiat redemption price.
- Official [Python SDK](https://github.com/Polymarket/py-sdk) inspected at
  `860ac384931ea55ed08a92219c9d27c02f6966cf`, files
  `src/polymarket/_internal/actions/perps/account.py` (`list_funding_payments`,
  `_descending_history`) and `src/polymarket/models/perps/account.py`.
- Official [TypeScript SDK](https://github.com/Polymarket/ts-sdk) inspected at
  `b268185c818a646a93693e09f57be323213530b9`, file
  `packages/client/src/websockets/perps/actions/account.ts`
  (`ListPerpsFundingPaymentsRequest`, `listPerpsFundingPayments`,
  `toPerpsDescendingAccountPage`). Source comments explicitly define inclusive
  start/end milliseconds. Both SDKs overlap the last boundary, deduplicate it,
  and move backward; their cursors are SDK-local state, **not REST cursor params**.

## Transport and account identity

The only permitted origin is `https://api.perpetuals.polymarket.com` (an optional
trailing slash is accepted). A configured foreign origin fails before IO, rather
than sending secrets to a caller-selected host. Requests are GET-only, with
redirects disabled. The shared HTTP transport raises on unsuccessful statuses.

Private headers are exactly `polymarket-proxy` and `polymarket-secret`, from the
explicit profile's `proxy_address` and `proxy_secret`. No SDK sessions, wallet
keys, signing, credential creation, POSTs, or environment fallback are used.
All profile credential shapes and same-proxy secret conflicts are checked before
the first network request. Exact proxy aliases are case-insensitively collapsed
only when their secrets agree.

For each distinct proxy, GET `/v1/account/credentials` resolves the canonical
account `address`. Its documented `keys` list must contain the authenticated proxy
with a well-formed expiry. Unknown/malformed owner identity or missing proxy
binding fails closed, even if a previous profile already produced amounts. The
collector never treats proxy address as economic account identity: multiple
proxies can belong to the same account.

Economic deduplication uses `(owner address, instrument_id, payment id)`, with
`(timestamp, funding, funding_rate, size)` as a conflict fingerprint. Identical
payments reached through multiple account keys count once; differing financial
payloads fail the entire venue. Identical IDs in different markets or owner
accounts remain distinct. These guarantees rely on the venue's documented
account-address and stable-ID contract, not guesses based on key similarity.
Missing account identity cannot silently become an independent account.

## Window, pagination and exact amounts

The shared builder supplies the latest completed **11:30–11:30 Europe/Moscow**
window. Source timestamps are integer **Unix milliseconds**, confirmed by the
OpenAPI `ts` definition and SDK epoch-to-UTC conversion; they are not naive local
datetimes. The collector accepts timezone-aware, millisecond-aligned inputs only,
queries inclusive `[start_ms, end_ms]`, and books exactly `[start_ms, end_ms)`.
It filters the inclusive upper edge and conservative outside-window rows.

REST returns `{data: [...], more: bool}`, at most 100 entries. A `more: true`
response is incomplete, regardless of its length. Instead of assuming ordering
or skipping same-millisecond ties, this implementation recursively partitions
inclusive integer ranges into `[lo, mid]` and `[mid+1, hi]`. Capped parents are
observed for consistency but **never booked**; only complete leaves contribute.
Every observed parent event must survive in complete leaves. Duplicate page IDs,
conflicting repeats, invalid leaf ranges, an empty capped page, a capped single
millisecond, an undocumented pagination envelope, request-budget exhaustion, and
transport errors all invalidate the venue. Thus it intentionally fails where an
SDK's boundary-minus-one fallback could silently skip unresolved ties.

The collection budget is 256 requests, 15 seconds per request and 120 seconds for
required account/history collection. Optional display metadata shares the
remaining deadline/budget. Failure is not turned into a zero or partial result.

Payment IDs remain Python integers (no float/JavaScript-safe-integer conversion).
Financial fields must be finite plain decimal strings. Exact coefficient-based
addition and unchanged source signs are independent of the caller's Decimal context,
including amounts larger than the default 28-digit precision. No rounding is
performed before display.

## Sign, timezone and pUSD: resolved contract versus remaining limitations

- **Sign contract:** `funding` is already **received-positive / paid-negative**
  signed account cashflow. Preserve it unchanged; do not negate it or infer its
  sign from size/rate. OpenAPI `fund`'s **“Funding paid in USD”** wording was
  previously misread as paid-positive. Sanitized live records contradict that
  interpretation and agree with the funding guide's positive-rate/long-pays rule:

  | Payment ID | Instrument | Size | Rate | Raw funding (pUSD) | Timestamp (ms) |
  | --- | --- | --- | --- | --- | --- |
  | 3848868085846259 | 50 | 800.00 | 0.00000625 | -0.032181 | 1792828800025 |
  | 616548028301290 | 50 | 800.00 | -0.00002084 | 0.107068 | 1792825200027 |

  The first long pays (negative cashflow); the second long receives (positive).
  The old negation produced -5.503694 for the supplied pair totals; unchanged
  signed amounts correctly give +6.909902. No rate-based reconstruction or
  absolute-value conversion is used. These records are API evidence, not
  independent proof of corresponding UI or balance changes.
- **Timezone contract:** Unix milliseconds remove the naive-timezone ambiguity.
  SDK UTC datetime conversion supports this interpretation. Live payment timing
  versus independently timestamped balance changes has not been reconciled.
- **Units contract:** the REST amount is explicitly documented in USD; the asset
  is validated as exactly `pUSD`, matching the account-history examples. The
  report's USD column represents the venue's **nominal USD accounting amount**,
  not a calculated market value or a guarantee of pUSD/USDC/fiat parity. It does
  not multiply token quantity by an assumed exchange rate. Other assets,
  including legacy `USDC` examples in the generic schema, fail closed until
  their actual REST semantics are verified and tested. Never silently relabel
  unknown settlement assets as USD.

Remaining uncertainty includes independent balance/UI reconciliation, retention
behavior, API access/eligibility across accounts, and live multi-page conformance.
The supplied nonzero debit/credit records resolve the mistaken sign interpretation;
they do not independently validate all identity, timing, unit or pagination
contracts. Authorized follow-up should reconcile those against independent account
evidence. An empty live window cannot settle nonzero-accounting questions.

## Presentation and all-or-nothing integration

GET `/v1/info/instruments` is optional, public and receives **no private headers**.
Only safe, unambiguous perpetual/pUSD display labels are used. Missing, retired,
unknown or unsafe metadata falls back to the raw instrument ID; it never filters
out valid funding on closed positions or changes amounts.

`FundingReportService` accepts `polymarket_api_base` with the official origin as
its default. Dependency construction forwards the configured field with a
`getattr` fallback for older settings doubles. It passes enabled refs only. Any
required page/profile failure discards **all Polymarket rows**, preserves other
venues, emits only a sanitized exception class, and marks the report incomplete.
Exception text, proxy addresses and secrets never appear in report warnings.
The automatic sender must continue to respect the existing complete-report gate.

## Tests

Run `pytest tests/test_polymarket_funding.py tests/test_polymarket_funding_integration.py -q`.
Coverage includes exact sanitized live debit/credit replay through raw HTTP JSON,
collector, report and renderer; synthetic aggregate reconciliation of +6.206798
Polymarket plus +0.703104 Hyperliquid to +6.909902; exact large decimals under
reduced precision; inclusive query
and exclusive report boundaries; account-wide closed/unknown markets; split
pagination and saturation; complete empty histories; request and time budgets;
full preflight; same-account proxy deduplication and conflicts; identity failures;
metadata fallback; configured/default dependency wiring; strict disabled isolation;
and failures after a successful leaf or profile while preserving another venue.
