# Bitget actual funding ledger

`src.services.bitget_funding.fetch_bitget_funding(store, refs, start, end,
request_json, api_base, *, account_mode='uta', now=None, sleep=asyncio.sleep,
max_pages=256)` returns signed `dict[str, Decimal]` nominal USD by underlying.
No positions snapshot is required, so closed contracts remain included. This
helper is wired into `FundingReportService` and the dependency factory for enabled
profiles. A venue failure discards its rows and marks the report incomplete while
retaining other venues. Wiring does not enable profiles, authorize delivery, send
messages, or trade.

## Account mode and official contract research

Verified public official documentation:

- [UTA financial records](https://www.bitget.com/docs/catalog/account/assets-balance#get-financial-records)
  documents `GET https://api.bitget.com/api/v3/account/financial-records`,
  permission **UTA mgt. (read)**, 20 requests/sec/UID, last 90 days,
  maximum 30-day query span, maximum `limit=100`, `data.list` and `data.cursor`.
- [UTA financial-record type enumeration](https://www.bitget.com/docs/uta/enum#type-financial-records)
  distinguishes four perpetual funding types:
  `CONTRACT_MAIN_SETTLE_FEE_USER_IN`, `CONTRACT_MAIN_SETTLE_FEE_USER_OUT`
  (cross margin), `MARGIN_SETTLE_FEE_USER_IN`, `MARGIN_SETTLE_FEE_USER_OUT`
  (isolated margin). RWA cash dividends are separate types and are not funding.
- The financial-record example includes `category`, `id`, `symbol`, `coin`,
  `type`, `positionType`, `fee`, `positionAmount`, `positionBalance`, `amount`,
  `balance`, `ts`. Records use their actual `amount`, never `fee` or a
  reconstruction from position size/rate. IN/OUT direction must agree with
  signed amount; contradictory data raises rather than silently changing sign.
- [Classic account bills](https://www.bitget.com/api-doc/classic/contract/account/Get-Account-Bill)
  documents `GET /api/v2/mix/account/bill`, `productType`,
  `businessType=contract_settle_fee`, millisecond `startTime`/`endTime`,
  `limit=100`, and `idLessThan` set from the preceding response's `endId`.
  Response is `data.bills` with `billId`, `symbol`, `amount`, `fee`,
  `feeByCoupon`, `businessType`, `coin`, `balance`, `cTime`.
  `onlyFunding=yes` does **not** mean funding-fee-only: it excludes four
  nonfinancial margin adjustments. It is not a substitute for businessType.
- Parent's authenticated probe of the configured disabled Bitget profile
  returned HTTP 400 / **40085**, explicitly stating that Unified Account mode
  does not support the Classic Account API. Classic success/empty cannot be
  assumed for this profile. This worker performed no authenticated probes and
  read no live credentials.

**Implemented mode: UTA only.** Passing any other `account_mode` raises before
network access. There is no automatic arbitrary-error fallback and no empty
result substituted for a mode/permission/API failure. Supporting classic later
requires a separate tested schema/cursor path, not guessing UTA compatibility.

Do not confuse `/api/v3/account/funding-financial-records` (the Funding wallet's
financial records: deposits, transfers, Earn, etc.) with actual derivatives
funding settlements. The correct route is **financial-records**, without the
`funding-` prefix.

## Exact parent live-probe recipe

With explicit profile key/secret/passphrase, sign a read-only GET for:

```
/api/v3/account/financial-records?category=USDT-FUTURES&type=CONTRACT_MAIN_SETTLE_FEE_USER_IN&startTime=<inclusive_epoch_ms>&endTime=<exclusive_epoch_ms_minus_1>&limit=100
```

Repeat for all four types above and `category=USDC-FUTURES`. The implementation
queries all eight streams; it never limits products to the older snapshot
connector's single configurable `bitget_product_type` / margin coin. The same
route without `type` is useful for an independent audit of filtering, signs and
presence of all funding variants. A nonzero live response should verify signed
`amount`, exact category/coin casing and terminal cursor shape before enablement.
The docs establish IN/OUT direction but their illustrated row is a spot-margin
trade, not a nonzero funding row: offline tests are not live accounting proof.

## Security, completeness and exactness

- Only `https://api.bitget.com` (optional trailing slash) accepted. GET only;
  `follow_redirects=False` required of injected transport. No order endpoints.
- Explicit credentials come only from each requested profile's store entry.
  All requested profiles are validated before any network request. Missing values
  raise, without environment/default-profile fallbacks; the same key with a
  different secret or passphrase raises a sanitized credential-conflict error
  with zero requests. Identical key/secret/passphrase aliases fetch once.
- Reuses `BitgetRealConnector._build_signed_headers` with explicit arguments:
  Base64 HMAC-SHA256 of `timestamp + GET + path + ?raw_encoded_query`.
  URL uses exactly the signed query. Pacing is 0.51 seconds between requests,
  across profiles/products/types, with fresh signing after the wait.
- Request/response failures raise sanitized errors. Callers must discard the
  entire venue's result on any later page/profile failure and preserve other
  venues themselves. Cancellation remains cancellation.
- API-key aliases fetched once; event IDs scoped to authenticated key/account.
  Distinct keys with the same event ID count independently. Different API keys
  for the same underlying account cannot be identified as aliases without a
  verified UID mapping and must not be configured twice.
- Requires complete schema, stable USDT or USDC settlement matching product,
  valid IDs/timestamps/symbols, expected funding type, consistent sign, and valid
  optional position type/side. Identical overlapping records deduplicate;
  conflicting records and nonprogress pages/cursors fail closed.
- Opaque cursors are URL-encoded and followed even on short or empty pages.
  A missing/non-string cursor is invalid. An empty string terminates; a full
  page without continuation fails conservatively. Global request cap is 256
  (not 256 per stream/profile); exhaustion raises with no partial return.
- Aware, completed, millisecond-aligned `[start, end)` windows only, within 90
  days and at most 30 days long. Inclusive API end is `end_ms - 1`; returned
  rows are also locally filtered. No historical extrapolation or FX conversion.
- Decimal string parsing and precision-sized local addition preserve exact
  money even when the caller lowers Decimal precision. USDT/USDC are treated
  nominally as USD, not market-converted. No display rounding here.

## Verification

Offline tests use synthetic credentials and injected transports only. They
cover exact wire signatures, all four funding types and both products, closed
symbols, boundaries, cursor traversal/encoding, >10,000 events, account-scoped
IDs, aliases, low-precision Decimal context, global budget, schema/sign/mode
failures, redirect policy, pacing, missing credentials and later failures.

Run `python3 -m pytest tests/test_bitget_funding.py -q` and
`python3 -m ruff check src/services/bitget_funding.py tests/test_bitget_funding.py`.
Shared report/dependency wiring is implemented and covered by
`tests/test_binance_bitget_funding_integration.py`; it is not proof of live access.
The parent-reported UTA probe returned **40014**, missing **UTA management READ**
permission. Bitget live funding remains **not verified**, including nonzero
amount/sign reconciliation. Keep the configured Bitget profile **disabled**;
permission changes, new probes and activation require separate operator action.
