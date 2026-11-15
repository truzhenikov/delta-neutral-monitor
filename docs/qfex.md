# QFEX read-only account connector (`qfex`)

## Support versus activation

Installed source support is **not** an activated or authenticated account. QFEX
is omitted from `ENABLED_EXCHANGES` defaults and the sample enabled list; a
missing credential store therefore does not activate it. This integration does
not create a production profile, change enabled venues, restart services, touch
Sheets, or enable scheduled funding collection. Reopen the Telegram setup menu
after deploying source to the bot to see `qfex`. The enable/disable menu lists
saved profiles only; without credentials there is intentionally no QFEX profile.

## Credentials and permissions

Create a dedicated API key in QFEX → profile → Developer Settings, with access
to the **specific intended primary account or subaccount** (not All accounts).
Provide only:

| Store field | Environment fallback (unnamed `qfex` only) | Meaning |
| --- | --- | --- |
| `api_key` | `QFEX_API_KEY` | PUBLIC API key |
| `api_secret` | `QFEX_API_SECRET` | SECRET API key, used literally as the HMAC key |
| `account_id` | `QFEX_ACCOUNT_ID` | Exact target account UUID, including for the primary account |

The explicit account UUID prevents accidentally monitoring the primary account
when a subaccount was intended. The connector sends it as
`x-qfex-requested-account-id`. Named profiles never borrow environment credentials
or another profile's keys. No wallet private key, password, JWT, MFA code, or
WebSocket session is needed.

Enable `view_positions` and `view_balance` for snapshots; enable `view_orders`
if the dedicated key is also intended for a later historical-funding probe.
Disable **`execute_orders`** and **`deposit_withdraw`**. Use a normal manually
created key, not a builder-issued trading key (builder keys can have additional
withdrawal capability). The monitor never tests permissions by attempting a write.
Local format validation is not authentication or proof of key restrictions.

Telegram `/setup qfex <profile>` collects these three fields using the shared
setup workflow. **As with other existing venues, finishing setup saves and
enables that profile.** Do not use it merely to stage unverified credentials.
Stage any candidate outside the production store, privately verify it, and obtain
explicit activation approval first. No credentials have been supplied for this
implementation.

## Documented API contract

All traffic goes to the fixed official origin `https://api.qfex.com`, with
`api_version=2026-10-29`. Redirects are disabled; arbitrary origins and trading
endpoints are not configurable or accepted. The connector performs only:

1. Public `GET /refdata`: validate the instrument's base/quote/margin assets.
2. Authenticated `GET /user/positions`: complete positions plus native balance.

The REST endpoint supplies the snapshot directly; no Trade WebSocket is needed.
The WebSocket documentation warns that initial cached balances can be zero before
an update, so subscribing to that stream is not a safer balance-query fallback.

Per the pinned OpenAPI `PublicKey` security scheme, sign the UTF-8 string
`<random_hex_nonce>:<unix_seconds>` using HMAC-SHA256 and the literal secret;
send a hexadecimal digest in `x-qfex-hmac-signature`, together with
`x-qfex-public-key`, `x-qfex-nonce`, `x-qfex-timestamp`, and the explicit account
header. Generate a fresh cryptographically random nonce for every authenticated
request. Neither the URL nor body is part of this documented signing grammar.
Public requests carry no private headers. HTTP errors are sanitized and never
include response bodies or supplied credentials.

## Financial mapping and limits

**Cross margin only.** Nonzero `isolated_equity`, nonzero isolated collateral or
availability, an explicit non-cross margin mode, or an isolated-only market fails
the whole account closed. The API schema has isolated fields despite an older
margin guide saying all products are cross-margined; do not silently mix them.

- `available_margin_usd = balance.available_balance` (not withdrawable cash).
- `maintenance_margin_usd = balance.position_margin`: the Definitions page
  explicitly calls Position Margin the maintenance requirement. Do not sum
  `PositionPublic.maintenance_margin` as dollars: the trade-stream example uses
  a dimensionless maintenance rate in that field.
- For cross accounts, `equity = available_balance + position_margin + order_margin`
  follows QFEX's published `Available Balance = Account Equity - Margin` and
  `Margin = Position Margin + Order Margin`. It includes PnL already. Do not add
  `unrealised_pnl` again or reconstruct cash from an incomplete REST fee breakdown.
- `position` is **signed base quantity**, not integer futures lots. QFEX defines
  position value as `mark × quantity` and unrealized PnL as
  `quantity × (mark - average_price)`, with no contract multiplier. `lot_size`
  describes the order increment, not a multiplier. Validate quote USD / margin
  USDC and the base-symbol mapping via reference data; reject unsupported markets.
- The REST position does not expose a mark. Recover its account valuation mark
  with the documented identity `mark = average_price + unrealised_pnl / position`.
  Do not substitute public `last_price`, `index_price`, or `underlier_price` for
  the mark: those have different definitions. Decimal parsing/calculation precedes
  conversion into the monitor's float models. A nonpositive/unrepresentable mark,
  malformed/nonfinite quantity, missing margin, duplicate market, or partial HTTP
  failure fails the entire snapshot. Long and short mappings have separate tests.
- Values are **nominal USDC collateral / USD quote**, not an independently fetched
  USDC/USD conversion. This follows the existing model's `*_usd` convention.
- Native per-position liquidation prices and a liquidation-state flag are absent
  from this REST contract. Keep `liquidation_price=None`, never zero or a generic
  leverage-derived estimate. The risk engine emits a QFEX-specific warning for
  open positions with unknown liquidation distance, so they cannot appear as
  fully known low risk merely because the portfolio net delta is hedged. This
  connector cannot supply liquidation-distance threshold alerts. Native account
  maintenance remains available; local cross/isolated risk still needs operator
  review before activation.
- `updated_at` is receipt time; this API response has no documented snapshot-age
  field. Receipt does not prove exchange-side freshness. Reconcile against the UI.

## Historical funding: deliberately not enabled

QFEX does have a documented account ledger, `GET /executions`, distinct from
public `/funding/{symbol}` rates and current `net_funding` counters. The execution
guide documents `execution_type=funding`, inclusive UTC ISO request bounds and
fractional Unix-second response `created_at`. It returns settled balance movements.

This change does **not** wire a QFEX daily funding collector or reconstruct
payments from current positions/rates. The pinned schema exposes `count` and a
maximum `limit=100` without a cursor/offset contract; completeness under capped
results, `amount` cashflow sign, supported `currency`/`state` values and actual
account permissions need a private nonempty ledger probe before promising exact
history. No fabricated private success or zero-funding fallback is supplied.
QFEX is not included in existing daily funding totals; this must be addressed
before approving its activation for scheduled reporting. Scheduled reports and
their configuration were intentionally left unchanged. A future collector should split saturated
windows safely, fail on saturated minimal intervals, and independently reconcile
signed amounts for the exact window before report activation.

## Verification evidence and activation gate

Synthetic tests cover signed wire headers, profile isolation, credential-format
validation, factory/mock/menu discovery, disabled defaults/missing-store fallback,
long/short base quantity, native maintenance, equity without PnL double counting,
null/empty positions, unsupported isolated state, numeric/malformed failures,
HTTP/redirect failures and visible unknown liquidation risk. These are **documented
mock fixtures**, not captured authenticated account payloads.

Public probes performed during implementation returned HTTP 200 for the YAML
contract and HTTP 200 JSON for both public metadata routes:

- `GET /contracts/2026-10-29/openapi.yaml` (published contract)
- `GET /refdata?api_version=2026-10-29`
- `GET /md/contracts?api_version=2026-10-29`

Public metadata proves route/contract availability only. No QFEX API credentials
were available; authenticated balances, nonzero positions, account identity,
key restrictions, native equity reconciliation, and execution history remain
**private-live unverified**. Before activation, use an isolated candidate store;
compare the intended account's UI and `/user/subaccounts/balance` native equity
with the snapshot, check both a short and long nonzero position where available,
and verify the missing-liquidation warning is acceptable. Restart only relevant
consumers after separate deployment/activation approval, verify `/v1/status`, and
reconcile downstream export mappings before enabling any scheduled output.

## Official sources

- [Pinned OpenAPI](https://api.qfex.com/contracts/2026-10-29/openapi.yaml)
- [API keys and permissions](https://docs.qfex.com/api-reference/api-keys.md)
- [User positions REST](https://docs.qfex.com/api-reference/rest/user/user-positions.md)
- [List account balances](https://docs.qfex.com/api-reference/rest/user/list-account-balances.md)
- [Definitions and formulae](https://docs.qfex.com/qfex/definitions.md)
- [Contract specifications](https://docs.qfex.com/qfex/contract-specifications.md)
- [Margin and liquidations](https://docs.qfex.com/qfex/liquidation-margin-call.md)
- [Account execution history](https://docs.qfex.com/api-reference/executions.md)
- [WebSocket balances and cached snapshot caveat](https://docs.qfex.com/websocket/channels/trade/balance.md)
