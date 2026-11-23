# QFEX actual funding history

The daily funding service reads authenticated **GET `/executions`**, pinned to
`api_version=2026-10-29`, filtered with `execution_type=funding`. It never uses
current quantity, implied rates, current positions, or PnL as funding.

## Contract and accounting

- Official account-history guide: https://docs.qfex.com/api-reference/executions.md
- Published schema: https://api.qfex.com/contracts/2026-10-29/openapi.yaml
- Funding description: https://docs.qfex.com/qfex/funding.md
- The guide calls these **settled account balance movements**, including funding
  credits and charges. Preserve native `amount`: positive received, negative paid.
- `created_at` is fractional Unix seconds; query boundaries are UTC ISO timestamps.
  The API accepts inclusive filters; the collector applies `[start,end)` locally.
- Keep the monitor's existing latest completed **11:30–11:30 Europe/Moscow** window.
- Monetary JSON numbers decode directly to Decimal. The live pinned contract emits
  numeric amount/timestamp fields (shown as strings by the evidence serializer).
- Only completed funding executions in USD/USDC and USD-quoted symbols are supported;
  other settlement states/assets/markets fail the entire venue closed.
- Discover account scope through the existing balance helper, with no account header
  on discovery; apply the resolved requested-account header on history. Ambiguous
  scope and permission/transport errors remain unknown, never successful zeros.

## Completeness

The published endpoint has limit 1–100, sorting and inclusive time filters, but no
cursor or offset. At the cap, recursively split inclusive intervals, discard capped
parent results, and deduplicate stable execution IDs at shared boundaries and across
profile aliases. Conflicting IDs, malformed rows, ignored filters, a saturated
one-microsecond interval, or exceeded request/deadline budget fail closed. No query
filters by current positions, so funding for closed markets remains eligible.

No archival-retention guarantee is published. Completeness means exhaustion of the
native filtered list, not an independently proven unlimited archive. Time-splitting
is tested with synthetic capped responses; the live account is too sparse to verify
server saturation behavior. Any account history permission failure is propagated;
successful balance access alone does not establish ledger access.

## Read-only live verification

Sanitized evidence: `/tmp/qfex-funding-live-evidence.json` (verification script:
`/tmp/qfex_funding_live_verify.py`). No credentials, account IDs, full signed URLs,
orders, margin changes, transfers, or withdrawals are included in the evidence.

On 2026-11-23 the latest completed window was Nov 21 11:30–Nov 22 11:30 MSK.
Collector, integrated builder and an independent limit-99 native query all returned
an empty daily ledger, without invented zero-symbol rows. The native funding history
was **not empty**: one completed PANW-USD charge **-0.01048746 USD**, Unix seconds
`1795417317.97814`. The collector reproduced that exact amount in a window covering
that historical payment. All nine authenticated verification GETs returned 200.
This confirms real nonzero native amount/sign transport; no claim is made that an
empty daily window alone proves accounting semantics. Sign interpretation rests on
the official settled balance-movement contract, not a guessed long/short convention.

Deployment verification on 2026-11-23 restarted the backend and bot, then checked
`/health` and `/v1/status`: QFEX PANW long 12 displayed calculated liquidation
266.0286459940805 (23.6055% distance at mark 348.23), with explicit calculated-price
provenance. Both services remained active; no financial/account state was changed.
