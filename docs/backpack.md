# Backpack account monitoring (read-only)

## Setup and scope

Identifier: `backpack`; named accounts use `backpack:<profile>`. The factory uses the existing no-argument constructor and assigns the exact profile reference before collection. Each named profile loads only its own `api_key` and `api_secret`; it never falls back to default/environment credentials. Unprofiled accounts may use `BACKPACK_API_KEY` / `BACKPACK_API_SECRET`.

Create a dedicated **read-only** key for the intended account/subaccount in Backpack. `api_key` is the base64 ED25519 public key (32 decoded bytes); `api_secret` is the base64 private seed (32 decoded bytes), not an expanded 64-byte secret. The connector derives and compares the public key before any network request. Local setup validation checks the pair but explicitly does not claim authenticated/live verification or verify venue-side key permissions. Never test permissions by submitting a write.

`/setup backpack <profile>` uses the existing credential-store setup behavior (saving explicitly entered credentials enables that profile). Merely deploying this connector does not create or enable a stored profile. The source supported-default list includes Backpack; the `.env.example` enabled list does not. No runtime `.env`, credentials, or session files are changed by this patch.

## Official contract and mapping

Sources:

- [Official API / embedded downloadable OpenAPI](https://docs.backpack.exchange/): `paths` and `components.schemas` in the Redoc page's `__redoc_state.spec.data` contain the complete schema (not just the truncated rendered documentation).
- [Margin system](https://support.backpack.exchange/exchange/trading/margin-system)
- [Technical margin](https://support.backpack.exchange/technical-docs/trading/margin)
- [Liquidation](https://support.backpack.exchange/technical-docs/trading/liquidation)
- [Futures specifications](https://support.backpack.exchange/technical-docs/trading/futures-specs)

The connector performs exactly two signed GETs, sequentially:

| Endpoint / instruction | Official schema | Monitor mapping |
|---|---|---|
| `/api/v1/capital/collateral` / `collateralQuery` | `MarginAccountSummary` | `netEquity` → equity; `netEquityAvailable` → available margin |
| same | `MarginAccountSummary.mmf`, `netExposureFutures` | maintenance = account MMF × account futures exposure, **only with zero borrow liability** |
| `/api/v1/position` / `positionQuery` | array of `FuturePositionWithMargin` | signed `netQuantity` → side and absolute base quantity; `entryPrice`, `markPrice`, `estLiquidationPrice` → prices |

`GET /api/v1/account` (`accountQuery`, `AccountSummary`) contains account settings/limits and fees, **not equity**; it is not needed for snapshot collection. `GET /api/v1/capital` (`balanceQuery`, token-keyed `Balance` objects) contains token quantities, not dollar equity, and is deliberately not summed as USD.

Net equity is already risk-adjusted collateral + unrealized PnL + unsettled balances − borrow liabilities. **Do not add `pnlUnrealized` again.** Available equity is already net equity minus locked equity, not withdrawable cash. All eligible collateral/lending and perpetuals share a cross-margin pool within the key's account/subaccount. Different subaccounts are separate; no other accounts are implicitly enumerated.

The official liquidation formula defines account MMF as weighted maintenance divided by total exposure. With **no borrow exposure**, `mmf * netExposureFutures` gives maintenance dollars, including potential open-position exposure supplied by the venue. MMF is **not MMR**: do not multiply it by equity. Accounts with nonzero `borrowLiability` fail closed for now, rather than undercounting borrow maintenance using an unverified exposure denominator. Empty positions are valid; account exposure may still include open orders.

Collateral records (`Collateral`) provide `assetMarkPrice`, `totalQuantity`, `balanceNotional` (USDC), `collateralValue` (haircut adjusted), and `collateralWeight`. The connector validates their financial values; it consumes the aggregate venue equity rather than revaluing or adding collateral twice. Unknown symbols are supported only with those explicit finite valuations; there is **no unknown-token = $1 fallback**. Collateral spot delta is not represented as a separate position; the reported position delta covers perpetuals only.

`netQuantity` is positive long / negative short; zero positions are omitted. Only `_USDC_PERP` position markets are accepted (other quote/market families fail closed). Position leverage is `1 / imf`, a **margin-implied leverage limit**, not a claim of actual isolated leverage. Liquidation is the venue's estimate (zero means unavailable), not an invented cross-margin formula. It is only a reference: other positions, collateral, interest, funding and open orders can change the actual liquidation boundary.

## Transport and failure behavior

The only accepted origin is exactly `https://api.backpack.exchange`; paths are explicitly allowlisted. Redirects are disabled. No POST, order, transfer, withdrawal, or write-permission probe exists in this connector.

Sign `instruction=<instruction>` + alphabetically sorted URL-encoded query parameters (if any) + `&timestamp=<milliseconds>&window=5000` using ED25519. Send base64 signature and public key in `X-Signature` / `X-API-Key` with `X-Timestamp` and `X-Window`. Generate the signature after request spacing, not before waiting. Current snapshot requests have no query parameters.

An aggregate `REQUEST_TIMEOUT_SEC` deadline covers both requests and spacing; HTTP requests also have that timeout. A 100 ms inter-request gap avoids a burst. No retries are made. HTTP errors expose status only; request errors and malformed JSON are sanitized without body, key, seed, headers or signature. Malformed/nonfinite required financial fields, duplicate/unsupported markets and incomplete account/position payloads fail the whole snapshot closed.

Public probes confirmed HTTP 200 JSON for `/api/v1/markets` and `/api/v1/markPrices`, including `BTC_USDC_PERP`, quote `USDC`. The connector does not need those extra requests because the authenticated positions schema supplies price and quantity directly. Synthetic raw-JSON tests verify signing and nonzero positions. The parent/operator also independently authenticated GET `/api/v1/account`, `/api/v1/capital/collateral` and `/api/v1/position`; a sanitized regression covers the flat account with USDC and priced SOL collateral (equity/available 2.13172746769, zero exposure/maintenance). This verifies neither nonzero live positions nor venue-side read-only permission. Runtime rollout remains a separate operator action.

## Funding ledger is not integrated

Backpack historical funding is **not yet supported by the funding-report service** in this patch. Do not infer daily funding from the current position's `cumulativeFundingPayment` or funding rate.

For a subsequent integration, the official endpoint is `GET /wapi/v1/history/funding`, instruction `fundingHistoryQueryAll`, returning `FundingPayment[]`. Query parameters: optional `symbol`, `limit` (default 100, maximum 1000), `offset` (default 0), `sortDirection`. Responses expose `X-PAGE-COUNT`, `X-CURRENT-PAGE`, `X-PAGE-SIZE`, `X-TOTAL`. `quantity` is positive received / negative paid; `intervalEndTimestamp` is a date-time string, with `symbol`, `fundingRate`, `userId`, optional `subaccountId`. Currency semantics, timestamp precision/timezone and complete pagination must be live-verified before integration; there is no documented time-window filter in this schema.
