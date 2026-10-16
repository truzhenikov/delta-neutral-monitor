# Extended actual historical funding

`src/services/extended_funding.py` reads the authenticated sub-account's actual
funding ledger. The shared `FundingReportService` includes it under `Extended`
for normally enabled `extended` profiles; disabled profiles are not queried.
An enabled profile with no payments contributes no rows and no warning, not a
synthetic zero. No profile or automatic-delivery setting is changed by this code.

## Wire contract and accounting

- Only production `https://api.starknet.extended.exchange` is trusted (an optional
  trailing slash is normalized). Redirects are disabled before sending secrets.
- `GET /api/v1/user/funding/history` uses `X-Api-Key` and
  `User-Agent: delta-neutral-monitor`. Stark private/public keys, vault numbers,
  client IDs, and signing are not needed for this read.
- Query `startTime` is required epoch **milliseconds**, calculated with integer
  arithmetic and floored to the enclosing millisecond. `limit=100`. There is no
  undocumented `endTime`, `market`, or `side` filter. Closed markets remain in
  scope without fetching current positions or market metadata.
- Records are attributed locally to exact timezone-aware `[start, end)` bounds
  using integer `paidTime` milliseconds. Later/out-of-window records do not stop
  traversal. All records, even outside the window, are validated.
- Sum the actual signed `fundingFee`: positive is received, negative is paid.
  Do not invert long-side amounts or reconstruct payments from rate/notional.
  Amounts are nominal USD for `-USD` markets, not an FX conversion.
- Money remains string-to-`Decimal`, with addition precision independent of the
  caller's decimal context. Market names are preserved exactly except the final
  `-USD`: `NVDA_24_5-USD` becomes `NVDA_24_5`, **not** a guessed `NVDA` ticker.

## Completeness and failure policy

`pagination.count` means the number of items in **this response**, not a global
total. Require its exact agreement with the page length. Follow the integer
cursor unchanged whenever present, including on short or empty pages. Missing
or null cursor explicitly ends traversal; page length never does. Pagination
must be present even on an empty response. This walks beyond the documented
10,000-record limit (a test exercises 10,100 records).

Repeated cursors, nonempty pages with no new profile events, malformed records,
conflicting duplicate identities, and request failures fail closed. Empty pages
with advancing cursors are followed within the global request budget. A maximum
of 256 requests applies across all profiles; subsequent requests are paced by
0.51 seconds. Budget exhaustion withholds the venue rather than publishing a
partial total.

Identical API-key aliases are queried once. Different keys are still fetched to
completion; records deduplicate by `(accountId, id)` across them. Each profile
must return one stable `accountId` across its pages. Duplicate event contents
must agree. Independent accounts contribute separately, even if event IDs match.
Empty histories have no returned account identity, but also no money to duplicate.

Validate all enabled profile keys before any history call. Missing/invalid
`api_key` produces an explicit safe warning. Any required profile or page failure
withholds **all Extended rows**, marks the shared report incomplete, and retains
other successful venues. Generic exception text/API error bodies are not shown
in reports, keeping credential values out of user-visible diagnostics. No
sampling/current-position fallback is used.

## Sources

- [Official API documentation](https://api.docs.extended.exchange/), sections
  **Pagination** and **Get funding payments**: cursor is the offset to pass to
  the next page, count is items in the response, required millisecond start time,
  actual payment fields and the 10,000-record pagination warning.
- [Official Python SDK pagination model](https://github.com/x10xchange/python_sdk/blob/starknet/x10/models/http.py):
  `PaginationModel.cursor: Optional[int] = None`, `count: int`;
  optional nullable response error. The SDK account module does not currently
  expose a funding-history wrapper; the endpoint contract comes from the docs.
- [Official Python SDK account endpoints](https://github.com/x10xchange/python_sdk/blob/starknet/x10/clients/rest/modules/account_module.py):
  authenticated reads pass API key and integer cursor/limit without signing.

No live credentials are included in fixtures. HTTP transport tests exercise
request encoding and blocked redirects with `httpx.MockTransport`.

## Verification

```sh
python3 -m pytest tests/test_extended_funding.py -q
python3 -m pytest tests/ -q
python3 -m ruff check src/services/extended_funding.py tests/test_extended_funding.py src/services/funding_report_service.py src/deps.py
git diff --check
```

The existing settings model supplies `extended_api_base`; dependency wiring
preserves its configured value. A default fallback supports older settings-like
callers that omit the new report dependency, without requiring unrelated fixture
edits. A nonproduction configured origin fails before the API key is sent.
