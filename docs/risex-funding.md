# RiseX historical funding

## Source and integration

Enabled `risex` profiles are included by `FundingReportService` and the dependency
factory. This uses public, read-only GETs on `https://api.rise.trade`:

- `/v1/markets` resolves market IDs, including inactive/deprecated markets.
- `/v1/account/funding-payments` reads all account markets, not just current
  positions. Closed positions therefore remain eligible.

Only a validated public EVM `account` address is consumed from the existing
credential store. No token, private key, signing, credential mutation, profile
activation, scheduler change, or delivery is performed by this collector.
The API origin is allowlisted and redirects are disabled.

Official contracts:

- [Funding history / OpenAPI](https://developer.rise.trade/reference/accountservice_getfundingpaymenthistory.md)
- [Funding sign convention](https://developer.rise.trade/reference/funding-channel.md)

Fetch the raw `.md` when a documentation extractor cuts off the OpenAPI schema.
The schema documents more detail than the endpoint's short introduction.

## Money, sign, and settlement

`funding_payment` is already decimal USDC, **not an x18 integer**. Native positive
means paid; native negative means received. The collector negates it exactly once
so report positive means received. Decimal strings are parsed without float;
addition uses a sufficiently large local precision even under a small caller
Decimal context. No rounding is applied in the collector. Legacy `amount_usd`
represents nominal USDC here; this is not an independent USDC/USD price valuation.

The history schema explicitly returns two states:

- `is_settled=true`: an on-chain settlement, with transaction hash and block.
- `is_settled=false`: an **unsettled hourly accrual**, with zero block and empty
  transaction hash. It is a venue-calculated account amount, not an estimate from
  current position/rate. It is **not yet a realized balance credit/debit**.

Both are included, but only `entry_type=funding`. One-off `dividend` adjustments
are excluded, not relabeled as funding. Do not sum this history again with a
cumulative position counter, WebSocket settlement events, or trade PnL; those are
not additional independent funding sources.

A record identity is account + market + timestamp + entry kind. Identical
pagination overlap is counted once. Conflicting amounts, sizes, rates, settlement
flags or transaction identities at the same key fail the entire venue closed;
a settlement transition is not counted twice. This API exposes no event/log ID
or snapshot cursor: two genuine records colliding at this key are deliberately
rejected rather than guessed apart. Different-timestamp revisions cannot be
recognized as the same economic accrual from this schema alone.

## Window and completeness

The shared report selects the latest completed **11:30–11:30 Europe/Moscow**
window. Boundaries are converted to Unix nanoseconds with integer arithmetic.
The request uses `start_time=start_ns`, `end_time=end_ns-1`, then filters every
record to the half-open `[start_ns, end_ns)` interval at full nanosecond precision.
No local/naive timestamp interpretation is used.

**Timestamp limitation:** the documented `block_time` is the block timestamp for
settled records but the hourly period end for unsettled records. Thus this is
funding attributed by the API's ledger timestamp, combining settled payments and
unsettled accruals. It is neither a settled-only cash ledger nor a proven
reconstruction of each historical economic accrual into its original hour.
A delayed settlement can fall in a different reporting window. Do not present
`complete=True` as proof of homogeneous accrual-time accounting or independently
reconciled balance cashflows.

Pagination is 1-based, `limit=1000`, `sorted_by=-time`, until the explicit boolean
`has_next_page=false`, with a global 256-page funding request budget across unique
accounts (market metadata is one additional request). Ascending `time` returned
false empty results in production reconnaissance despite being documented, so
it is not used. Account aliases are deduplicated case-insensitively before reads.

Every page must echo the requested account/page and have a valid schema. Repeated
nonempty pages with no new identities fail even when the repeated terminal page
claims there is no next page. A page/profile failure discards **all RiseX rows**,
adds a sanitized warning and marks the report incomplete; other venues survive.
A valid empty result creates no invented zero rows. Missing market metadata,
unsupported currency, unknown row types, malformed amounts, and API error
payloads also fail closed. No raw response/account is placed in report errors.

Nonempty RiseX contributions carry an operator-visible warning about unsettled
amounts and settlement-time attribution; the shared report is therefore marked
`complete=False` rather than asserting exact daily economic attribution. Empty
successful responses remain complete. The API offers
no immutable snapshot, independent total count, or documented retention guarantee;
concurrent settlement can change page contents, and silently omitted backend
records cannot be ruled out by a successful traversal. The implementation does
not claim coverage beyond what the endpoint actually returns.

## Verification evidence

Read-only live comparison on 2026-10-28 used independent pagination at five rows
per page versus the integrated collector at 1000 rows per page, with Decimal
sums and identical windows:

| Window (MSK) | Funding records | State | Independent = builder (USDC) |
| --- | ---: | --- | --- |
| 2026-10-27 11:30 → 2026-10-28 11:30 | 12 across 3 pages | 12 unsettled | BZ `+1.24357601613445685` |
| 2026-08-02 11:30 → 2026-08-03 11:30 | 1 | 1 settled, closed-history path | SNDK `-0.591261686524445478` |

The 90-day discovery response had 85 records, 73 settled and 12 unsettled, with
`has_next_page=false`. This proves those sampled historical records are returned,
not a 90-day retention SLA. Credential-store bytes were unchanged after the
verifier. The verifier does not independently reconcile balances or prove how
a live unsettled row is replaced on eventual settlement across report windows.

Tests cover genuine sanitized unsettled payloads, settled money/sign and boundary
contracts, closed/deprecated market mapping, overlap and transition conflicts,
page/profile partial failure rollback, account preflight and aliases, malicious
origin/redirect protection, malformed/error envelopes, Decimal precision, empty
and disabled profiles, dependency wiring, and renderer integration.

This source change alone does not restart any service or prove deployed scheduled
report behavior. Deploy/restart/delivery remain separate operator actions.
