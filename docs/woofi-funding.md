# WOOFi Pro / Orderly historical daily funding

## Integration contract

```python
async def fetch_woofi_funding(
    store: CredentialStore,
    refs: list[str],
    start: datetime,
    end: datetime,
    request_json: Callable[..., Awaitable[Any]],
    api_base: str,
) -> dict[str, Decimal]: ...
```

Pass existing `settings.woofi_api_base` unchanged: the only accepted value is
`https://api.orderly.org`. Pass every enabled WOOFi profile together. This module
is wired into the existing report builder through `get_funding_report_service`;
it changes no runtime credentials, subscriptions, or automatic-delivery setting.

Returns symbol -> exact signed **USDC accrual**, positive received, negative paid.
USDC is nominal USD for the existing report, not a fetched FX conversion. Only
`PERP_<symbol>_USDC` markets are accepted; USDT/coin settlement is rejected.
Historical `funding_fee` values are used directly, not reconstructed from rates,
current positions, or current cumulative funding. Both `Accrued` and `Settled`
records count once: these are lifecycle statuses of historical funding accruals.
**This is not a settled-cash-only report.** Pending accruals must not be described
as already transferred cash. Unknown statuses fail closed.

`request_json` must reject non-2xx HTTP responses and decode decimal JSON numbers
with `response.json(parse_float=Decimal)`; float monetary values are rejected.
Each call receives method `GET`, the exact already-encoded URL, explicit auth
headers, and `follow_redirects=False`. Do not add/reorder/re-encode query params.
The module suppresses raw transport exceptions and never logs auth/URLs/payloads.

## Official evidence

Reviewed during implementation:

- [Private funding-fee history](https://orderly.network/docs/build-on-omnichain/restful-api/private/get-funding-fee-history.md):
  `GET /v1/funding_fee/history`, omit symbol for account-wide history, 13-digit
  millisecond `start_t`/`end_t`, page starting at 1, default size 60, pagination
  metadata `total`, `current_page`, `records_per_page`. Rows contain `funding_fee`,
  `payment_type` (`Receive`/`Pay`), `status` (`Accrued`/`Settled`), `created_time`,
  `updated_time`, and `margin_mode` (`CROSS`/`ISOLATED`). Limit is 20 requests per
  60 seconds per user.
- [Funding payments field semantics](https://orderly.network/docs/build-on-omnichain/public-info-api/account/funding-payments):
  explicitly documents positive `funding_fee` as user pays and negative as user
  receives; distinguishes accrued/pending from settled. This is corroborating
  semantics only; the module does not call that separate endpoint or assume its
  cursor/timestamp fields exist in the private API. The live private response
  independently confirmed negative fees with `payment_type=Receive`.
- [Orderly perpetual contracts](https://orderly.network/blog/orderly-network-for-perpetual-futures-trading):
  contracts are quoted and settled in USDC.
- [Funding-rate mechanics](https://orderly.network/docs/introduction/trade-on-orderly/perpetual-futures/funding-rate):
  funding cash flows exchange between long and short holders at market-specific
  intervals. The module does not assume exactly three events or an 8h schedule.

The private endpoint documentation states **no retention horizon, maximum size,
maximum query window, or stable snapshot guarantee**. No invented 7/30/90-day
retention claim or arbitrary unsupported size is encoded. Latest completed daily
window was proven live below. Older empty histories prove only that the endpoint
returned no rows, not account lifetime coverage or a guaranteed retention period.

## Attribution, exactness, pagination, and security

- Inputs are aware millisecond-aligned datetimes. Convert to integer milliseconds
  with integer arithmetic, never floating timestamp multiplication. Query an
  inclusive envelope `[start_ms,end_ms]`; locally attribute by the private ledger's
  **`created_time`**, accepting `start_ms <= created_time < end_ms`. Reject fractional
  milliseconds rather than truncate them. This is ledger creation time, not an
  undocumented funding-epoch field, and never mutable `updated_time`.
- Fees are signed opposite the reporting convention, so exact `copy_negate()` is
  used (unary negation can round in the caller's Decimal context). Addition gets a
  locally sized context. Tests exercise huge amounts at precision 4 and tiny
  amounts decoded straight from raw JSON.
- Read all pages using size 60. Validate exact requested page/size, nonnegative
  integer total, stable total across pages, and exact expected row count on every
  page. A short page before the declared total, metadata drift, or page failure
  invalidates the entire venue. A full final page is complete when its verified
  metadata total is reached; no timestamp cursor can skip tied events.
- No stable record ID exists. Reject duplicate `(symbol,created_time,margin_mode)`
  identities across/within pages rather than guessing whether to double-count or
  discard them. Equal times for distinct symbols/margin compartments are valid.
  Reordering/mutation without changed totals cannot be absolutely detected because
  the server offers no snapshot token; this is an upstream API limitation.
- Bound total requests across all profiles to 256. Before every request after the
  first, wait 3.05s, then generate a fresh signing timestamp. Exhaustion fails
  closed, including saturated multi-page histories; no partial results escape.
- Validate the exact trusted production base **before reading credentials**.
  Credentials come only from the explicit `store.get_exchange_credentials(ref)`.
  Decode base58 private bytes and sign Ed25519 over
  `timestamp + GET + exact_path_with_query`, URL-safe base64 signature without
  padding; use the account/key/timestamp/signature Orderly headers. No inherited
  signer method may reload a default profile. Redirects are disabled.
- Deduplicate profile aliases by `account_id`, not API key: multiple keys can
  expose the same account-wide history. Validate every supplied credential profile.
  A failure on a later distinct profile invalidates earlier totals too. The caller
  must catch `ValueError` and suppress the whole venue, preserving other venues.

## Sanitized live verification

Observed at **2026-10-16T00:34:04.132514+00:00**, using the live machine clock.
Latest completed report window then was:

- UTC: **[2026-10-14 08:30:00, 2026-10-15 08:30:00)**.
- Europe/Moscow: **[2026-10-14 11:30:00, 2026-10-15 11:30:00)**.
- Exact query boundary milliseconds: **1791966600000**, **1792053000000**.
- One enabled distinct Orderly account; authenticated GET returned HTTP 200 and
  `success=true`. No order/transfer/write endpoint was called.
- Independent implementation forced **size 2**, verified **2 pages / 3 records**
  against metadata, independently signed each request and accumulated negative
  raw fees using Python `Fraction` (not the production Decimal algorithm).
- Production module separately fetched **1 size-60 page / 3 records**, with caller
  Decimal precision deliberately reduced to **4**. Its full exact dictionary
  equalled the independent Fraction totals converted at precision 80.

| Symbol | Exact received-positive USDC total |
|---|---:|
| NVDA | 4.61300000 |

All three live rows were `Accrued`, not `Settled`. They validate nonzero actual
historical **accrual** accounting, not finalized cash settlement. The records'
creation milliseconds were 1791993602872, 1792022406200, 1792051202603. A sanitized
live example had `funding_fee=-1.52600000`, `payment_type=Receive`,
`symbol=PERP_NVDA_USDC`, and `margin_mode=CROSS`.

## Tests and RED -> GREEN evidence

Before creating production code, `python3 -m pytest tests/test_woofi_funding.py -q`
reported **44 failed**, including the explicit module-existence assertion, because
the feature did not exist. After implementation the same file reported **44 passed**.
Five extra transport/alias/compartment/pagination regression cases then passed
without further behavioral changes: **49 passed**.

Coverage includes actual Ed25519 verification of `httpx` wire bytes, exact query
boundaries, GET-only/no redirects, explicit profile signing, aliases by account,
page-boundary ties/full terminal pages, later-page and later-profile failures,
request budget, pacing-before-fresh-signing, malformed/nonfinite/float amounts,
contradictory direction/currency, duplicate identities, missing credentials,
invalid windows, raw JSON Decimal transport, and precision-4 huge-amount sums.

Shared builder wiring, full-suite verification, deployment, and automatic delivery
are intentionally outside this module's ownership.
