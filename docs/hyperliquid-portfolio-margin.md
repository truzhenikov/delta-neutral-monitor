# Hyperliquid portfolio-margin free balance

## Scope and mapping

For a `spotClearinghouseState` response with **`portfolioMarginEnabled: true`**, the connector maps the canonical USDC (token **0**) entry of `tokenToAvailableAfterMaintenance` into `AccountSnapshot.available_margin_usd`. This is **venue-reported USDC availability after maintenance**, not a promise that the same amount can be withdrawn, transferred, or spent on every instrument. `*_usd` retains the monitor's existing nominal-USDC convention, not an independently priced USD guarantee.

The value replaces, rather than adds to, the sum of per-DEX `clearinghouseState.withdrawable` values. Other token entries are not added as dollars: BTC, HYPE, USDT, USDH, and independent borrowing capacities cannot safely be summed at a one-dollar exchange rate. This is deliberately a conservative USDC-only view, not a full multi-collateral buying-power implementation. Order-size limits, initial margin, collateral/borrow caps and the relevant order form's Available to Trade can impose additional restrictions.

An absent field, absent USDC tuple, malformed amount, non-finite/unrepresentable amount, malformed token ID, or duplicate token ID causes `RealConnectorRequestError`. An empty array is **unknown**, not a certified zero. A valid explicit zero is zero; finite negative availability is conservatively clamped to zero. The common snapshot model has no unknown-value or provenance field, so the connector fails instead of publishing a fabricated successful zero. Errors are sanitized and do not contain account identifiers or raw payloads.

Accounts without the explicit true portfolio-margin flag retain the existing ordinary-account mapping. No account mode, profile enablement, credentials, position mode, orders, margin, or transfers are changed. This patch adds no network requests.

Equity mapping is unchanged: the existing spot portfolio valuation takes precedence when positive; perps account values are not added to that unified balance. `supplied` is not added to `total`, and neither supplied nor perps equity is added to available balance. This narrowly scoped fix does not claim to solve pre-existing general multi-collateral/debt/equity valuation limitations.

## Evidence and its limits

Checked against these public sources; no live trading/signing credentials are needed:

1. **Official spot Info API documentation** — [Retrieve a user's token balances](https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/spot#retrieve-a-users-token-balances): “Under unified account or portfolio margin, this is the source of truth for trading account balance across spot and perps.” The metadata example identifies canonical USDC as token/index 0. This establishes the unified source and why adding perps balances again is wrong. The published example does **not** enumerate the newer portfolio-specific fields.
2. **Official portfolio-margin specification** — [Portfolio margin](https://hyperliquid.gitbook.io/hyperliquid-docs/trading/portfolio-margin): spot and perps trade from a single unified balance, all HIP-3 DEXs are included, eligible idle assets earn yield, and borrowing is subject to asset-specific LTV and caps. It distinguishes cross-margin pooling from subaccount separation. This does not make an isolated position cross-margin.
3. **Official FAQ** — [Portfolio margin](https://hyperliquid.gitbook.io/hyperliquid-docs/support/faq/portfolio-margin), especially Auto borrowing, Yield on idle assets, Available Balance, and Fees and caps: idle USDC is automatically supplied; borrowable balance can be used for trading but **cannot be withdrawn or transferred**; quote-asset available-to-trade figures can differ across DEXs; fully lent supply can block withdrawals. Therefore `supplied` is a lending/accounting state, not an additional independent cash pot or a withdrawal guarantee. Neither `total - hold` nor `supplied` is used to reconstruct portfolio availability.
4. **SDK field-level corroboration (community, NOT the official Python SDK)** — [`@nktkas/hyperliquid` spotClearinghouseState schema](https://github.com/nktkas/hyperliquid/blob/db80d598f6e4672edc090fe69994ecec97ebc980/src/api/info/_methods/spotClearinghouseState.ts), inspected at revision `db80d598f6e4672edc090fe69994ecec97ebc980`: `tokenToAvailableAfterMaintenance` is documented as “Amount available after maintenance per token”; each pair is `[token, amount]`. `hold` is “Amount on hold”, `spotHold` is “Amount on hold in spot”, and `supplied` is “Supplied amount”. The [official Python SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/info.py) confirms the read request through `spot_user_state`, but returns `Any` rather than defining these newer field semantics. Do not misattribute the community schema to Hyperliquid itself.
5. **Official production application, independent field-level confirmation** — [config-9K9nKa9w.js](https://app.hyperliquid.xyz/assets/config-9K9nKa9w.js), referenced by [index-EumEeWvY.js](https://app.hyperliquid.xyz/assets/index-EumEeWvY.js) during this investigation. The response adapter `kA` maps `tokenToAvailableAfterMaintenance` to `tokenNameToAvailableAfterMaintenance` using the token metadata. The cross/unified liquidation calculation selects the DEX collateral token's native availability in place of legacy `accountValue - crossMaintenanceMarginUsed`:

   ```js
   (j && a.type === `cross`
     ? p.tokenNameToAvailableAfterMaintenance?.find(([e]) => M === e)?.[1] ?? 0
     : w - T)
   ```

   Here `j` is the unified/portfolio account-mode check and `M` is `m.collateralToken`. This demonstrates that the field is used as a margin resource, not merely a spot display balance. The same bundle carries `total`, `hold`, `spotHold`, `supplied`, and `borrowed` separately; helper `X8` uses `spotHold ?? hold`, while `Z8` uses `hold`, and `Q8` adds borrow/supply-dependent logic. These are not universally interchangeable “free cash” formulas. Bundle URLs and minified names are evidence of the inspected release, not a stable API contract. This connector does not copy the UI's missing-field-to-zero behavior.

Together, the official unified-balance contract, production application's use of the actual field for margin, and SDK tuple semantics support the mapping. They do **not** establish universal order-specific buying power or withdrawability. No withdrawal, transfer, borrow, or isolated-margin mutation was tested.

## Sanitized incident fixture

The parent investigation observed profile `hl-3c3b` with roughly 4,755.26 monitor equity, zero available balance and zero reported cross maintenance despite three HIP-3 positions. Public account reads showed:

| Surface | Field | Observed value |
|---|---|---|
| `userAbstraction` | mode | `portfolioMargin` |
| `spotClearinghouseState` | `portfolioMarginEnabled` | `true` |
| spot USDC | `total` | `4756.26203398` |
| spot USDC | `hold` / `spotHold` | `3753.791463` |
| spot USDC | `supplied` | `1002.47057098` |
| native availability | token 0 | `1002.47057098` |
| xyz perps | `marginSummary.accountValue` / `totalMarginUsed` | `3754.011463` |
| xyz perps | cross summary / `withdrawable` | zero |
| positions | native `leverage.type` | **`isolated` for all three** |

These were separate, time-dependent reads; the slightly different monitor equity is not an exact synchronized reconciliation. The regression preserves the reported balance shape with a synthetic wallet and synthetic position. Before the fix it reproduced **0.0** instead of **1002.47057098**. After the fix, that is the unrounded snapshot availability. A separate fixture intentionally makes native availability different from both `total - hold` and `supplied` to prevent an accidental fallback formula from passing.

## Isolated positions and recommendation safety

Portfolio-margin account mode is **not** the same thing as a position's leverage/margin mode. The incident's three positions are actually isolated. The common `Position` model currently retains leverage value but **discards `leverage.type`**. Account cross maintenance of zero and portfolio margin ratio of zero therefore do not prove that those isolated positions are protected or have no liquidation risk.

A recommendation cron must obtain each position's native `leverage.type` (plus current isolated margin, liquidation distance and the matching settlement asset/DEX) from fresh read-only account state before suggesting an action. Do not infer cross mode from portfolio-margin enablement or from a positive account-level free balance. This patch intentionally does not expand the common model or modify cron behavior.

When evaluating a recipient needing margin, **first evaluate its own free USDC and, for isolated positions, a venue-native isolated-margin adjustment**, with fresh preflight and explicit user approval. Additional account free balance does not automatically increase an isolated position's allocated margin. Only assess an external donor if own usable funds are insufficient. Never automatically transfer funds, reduce another position's isolated margin, or equate this available balance with a verified transferable donor budget.

## Verification

Run with the working test interpreter (`python3`; the repository `.venv` has no pytest):

```sh
python3 -m pytest tests/test_hyperliquid_portfolio_available.py tests/test_hyperliquid_connector.py -q
python3 -m pytest tests/ -q
```

Strict RED run: 25 failing / 4 passing initial cases, including the exact zero-versus-native-availability assertion. A subsequent extreme-exponent case also failed before its validation fix. GREEN: 30 new cases plus 8 existing connector cases passed. Full suite at final verification: 1,420 passed, one unrelated Google Python-version deprecation warning. Tests cover replacement not double-counting, profile identity, unchanged equity/positions/cross maintenance, explicit/missing mode legacy behavior, missing field/USDC, malformed tuples/IDs, duplicate IDs, non-finite and float-overflow amounts, zero/negative handling, and exclusion of non-USDC token amounts. No deployment, commit, runtime enablement, or mutation is part of this change.
