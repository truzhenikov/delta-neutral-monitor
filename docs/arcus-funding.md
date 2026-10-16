# Arcus historical funding report

`src/services/arcus_funding.py` reads **actual signed payments**, not estimated
funding reconstructed from current positions or rates. It uses only public
`address` and explicitly configured `account_index` identity fields. No signing,
API key, private key, cookie, or authenticated endpoint is needed.

## Source contract

The official Arcus OpenAPI specification (`https://docs.arcus.xyz/`,
`/api-reference/openapi.yml`, `GET /v1/funding`, `FundingPayment` and
`GetFundingPaymentsResponse`) documents:

- Production origin: `https://api.arcus.xyz` (the only accepted origin).
- Query: `address`, `accountIndex` (0–9), `from`, `to`, `limit=1000`.
  Omit `market` so history includes all markets, including closed positions.
- Bounds are **inclusive epoch microseconds**, at least `1e14`. Funding events
  occupy whole-millisecond ticks; mid-millisecond bounds narrow the range.
- `fundingPayments` is required; `total` is optional. When present, `total`
  counts **returned rows**, not all matching history, and must equal array length.
- The native maximum is 1000; larger requested limits silently clamp. There is
  no documented cursor here. A full page is never proof of complete coverage.
- Each payment requires `marketId`, `marketDisplayName`, `fundingRate`, signed
  `size`, signed `payment`, and integer-microsecond `time`.
- `payment` is quote currency applied: positive means received, negative means
  paid. USD/USDG is reported at nominal USD, without a conversion or sign flip.

The report strips only the exact `-USD` display suffix. It never requests current
positions/market metadata to infer historical labels. Decimal aggregation uses a
sized local context, independent of the caller's Decimal precision.

## Completeness and safety gates

Convert the existing half-open report interval `[start, end)` into integer ticks
`[ceil(start_us/1000), ceil(end_us/1000))`. A tick interval `[low, high)` maps to
wire bounds `from=low*1000`, `to=(high-1)*1000`. Split saturated intervals at an
integer midpoint into disjoint children. Never count saturated parent payments.
This avoids timestamp-tie loss and fractional-bound narrowing gaps, and uses no
floating-point timestamp arithmetic.

Canonical lowercase address plus account index deduplicates aliases; separate
subaccounts remain separate. Validate **all** configured identities before any
GET, without inferring missing account index zero. Within each account, dedupe
identical `(marketId, time)` events and reject conflicts, including parent/child
conflicts. This identity follows the vendor's one-event-per-market/account/time
contract.

Requests are bounded to 256 per venue report and spaced by 0.51 seconds after
the first, including across profiles. Saturated single-millisecond leaves,
request-budget exhaustion, malformed records/pages, explicit contradictory
currencies, business failures, interval violations, and profile/request failures
withhold **all Arcus rows** and mark the shared report incomplete. Other venues'
valid rows remain. Warnings expose exception class, not server payload/address.
No automatic-delivery default or other venue behavior changes.

## Verification and limitations

`tests/test_arcus_funding.py` uses synthetic public identities and mocked
transports only. It covers actual sign/amounts, optional returned-page totals,
closed-market labels, reduced Decimal precision, alias/subaccount identity,
input/schema failures, capped-parent exclusion, tied all-market interval
splitting, tick boundaries, conflicts, request limits, pacing, builder rollback
after partial profile/split success, and dependency wiring.

No live API calls, production credential reads, deployment, or commits are part
of this implementation verification. Independent live semantic comparison is a
separate operator/parent verification step. Completeness depends on the vendor
honoring the documented cap/tick/history contract and retention. Rate limiting
or unexpectedly dense history fails closed rather than emitting a partial sum;
the fixed spacing does not guarantee that a shared IP bucket cannot be exhausted.
