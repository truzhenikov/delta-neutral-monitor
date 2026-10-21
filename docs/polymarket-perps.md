# Polymarket Perps (`polymarket`)

Read-only **perpetual futures** account monitoring, not Polymarket binary prediction-market/CLOB balances. Label: **Polymarket Perps**. Support is opt-in and is deliberately absent from both `Settings.enabled_exchanges` defaults and `.env.example`'s enabled list. Source support does not activate a runtime account.

## Credentials and safety

Only existing delegated credentials are consumed:

- `proxy_address`: the registered proxy's 20-byte `0x` EVM address, **not** an assumed owner-wallet address.
- `proxy_secret`: its REST authentication secret.

No owner private key, proxy private key, signature or SDK session registration is accepted or needed by this connector. Registration, renewal, orders, collateral transfers, proxy creation and revocation are out of scope. The official authenticated-sessions guide says delegated credentials expire after one week by default; expiration requires operator renewal outside this monitor. The secret may authorize more than reads remotely: this connector is GET-only, not proof that the credential is exchange-enforced read-only.

Profile-scoped storage uses the existing `/setup polymarket <profile>` flow. This operator action follows the application's existing save-and-enable behavior; do not run it just to install source support. Setup validation checks format only and explicitly does **not** claim live verification. The secret is masked in credential listings. Unknown/private-key fields are not persisted by the credential allowlist, and direct connector credential validation rejects them. Named profiles never borrow default-profile or environment credentials. The unprofiled connector can use `POLYMARKET_PROXY_ADDRESS` and `POLYMARKET_PROXY_SECRET` only when it has no stored credential entry.

Transport is pinned to `https://api.perpetuals.polymarket.com`, without a configurable host. There is a two-path allowlist:

1. `GET /v1/account/portfolio`, headers `polymarket-proxy` and `polymarket-secret`.
2. `GET /v1/info/tickers`, **no private headers**, for current mark prices.

Redirects are disabled explicitly at client and request level. HTTP errors report only the status; network/JSON errors are generic, never response bodies or secret-bearing exceptions. One aggregate `REQUEST_TIMEOUT_SEC` deadline covers both requests and parsing, in addition to HTTP timeouts. No retries or mutations are performed.

## Official schema evidence

Fetched the complete raw official Markdown pages (not just text-extractor output, which omits some tabs):

- [Account management](https://docs.polymarket.com/perps/account-management.md): full REST portfolio example, snake_case financial fields.
- [Authenticated sessions](https://docs.polymarket.com/perps/authenticated-sessions.md): the “Resume a Session / API” section distinguishes `proxy_private_key` for signed trading from `proxy_secret` for private REST reads and shows the two exact portfolio headers.
- [Trading](https://docs.polymarket.com/perps/trading.md): “Choose Direction and Size / API” supplies the full portfolio JSON; positive position `size` means long and negative means short.
- [Concepts](https://docs.polymarket.com/perps/concepts.md): mark price values positions; account equity includes open-position gains/losses, fees and funding; collateral is pUSD.
- [Market data](https://docs.polymarket.com/perps/market-data.md): “Fetch Tickers / API” specifies an array from `/v1/info/tickers` with `instrument_id`, `symbol`, `mark_price`, `timestamp`; snapshots normally lag by at most ten seconds, otherwise the gateway should return 503.

### Mapping

| REST field | Monitor mapping |
| --- | --- |
| `margin.total_account_value` | `equity_usd`, directly; never add unrealized PnL again |
| `margin.available_order_margin` | `available_margin_usd`, not `withdrawable` |
| `margin.total_maintenance_margin` | `maintenance_margin_usd`, directly |
| `positions[].symbol` | Preserve exact venue market identifier |
| `positions[].size` | Sign determines side; absolute size is quantity; zero position omitted |
| `positions[].entry_price` | Entry price |
| public ticker `mark_price` | Current mark, matched by **both** instrument ID and symbol |
| `positions[].leverage` | Venue leverage, not a guessed margin ratio |
| `positions[].liquidation_price` | Positive value or unavailable (`null`, missing, zero) |
| `timestamp` | Source portfolio UTC timestamp, not poll-completion time |

`position_value`, initial/maintenance position margins, unrealized PnL, cumulative funding, total initial margin, total position value and withdrawable are validated, but not added together to reconstruct equity. `cross` and `in_liquidation` must be booleans. An active `in_liquidation=true` fails closed with an explicit `account in liquidation` error: the account must never be displayed as successfully healthy while liquidation is active. The common snapshot model has no margin-mode, liquidation-state, withdrawable, or cumulative-funding fields; this connector does not invent replacements. Current position PnL/notional/delta remain the shared model's mark-based calculations. The ticker and portfolio are separate snapshots, so their PnL may differ slightly during price movement; the connector does not back-solve an undocumented mark from `position_value / size` or entry/PnL.

**Units limitation:** the shared model calls values `*_usd`, but these are **nominal pUSD amounts** and pUSD prices per quantity unit. No external pUSD/USD FX conversion or independently verified peg is supplied. Cross-venue totals therefore retain that nominal-unit assumption; do not describe these as independently FX-verified USD valuations.

All required numeric fields reject booleans, malformed data, NaN, infinity and float overflow. Nonnegative fields reject negative values. Active positions require positive entry, leverage and mark. Missing/duplicate IDs or symbols, missing marks, schema/error envelopes and malformed timestamps fail the snapshot closed. Source portfolio and ticker timestamps must be within 120 seconds in the past or 30 seconds in the future (local safety policy, not an assertion of the venue SLA). Float arithmetic overflow in derived position notional/PnL is also rejected.

## Availability probe and live limitations

Actual unauthenticated HTTPS GET probes from the implementation environment returned:

| Endpoint | Result |
| --- | --- |
| `/v1/info/instruments` | HTTP 200, JSON array; e.g. instrument 1 `SP500-USD`, `quote_asset: "pUSD"` |
| `/v1/info/tickers` | HTTP 200, JSON array; instrument 1 had `index_price: "7731.2"`, `mark_price: "7730.8"`, `last_price: "7733.3"`, `timestamp: 1792569170014` |
| `/v1/account/portfolio` without headers | HTTP 401, JSON `{"status":"err","error":"unauthorized"}` |

These are actual public observations, not authenticated account results. **Private live verification is BLOCKED: no proxy credentials were supplied.** Nonzero account values/positions, liquidation mapping, account identity, and UI reconciliation have not been live-verified. Test account fixtures are explicitly synthetic, based on the official raw REST examples, with refreshed test timestamps; they are not fabricated live output. Nothing was registered, funded, enabled, deployed, committed or pushed. No runtime profiles or secret `.env` files were edited.

## Tests

`python3 -m pytest tests/test_polymarket_connector.py -q` exercises raw HTTP JSON mapping; long/short/flat and missing liquidation; signed-size semantics; mark versus last price; equity without double UPNL; finite/schema validation; duplicate/stale markets; sanitized failures/no redirect; profile isolation/no environment fallback; aggregate deadline; registry/factory/mock/setup labels; credential masking and 0600 store persistence; local validation without a live-verification claim; and missing-store default-off behavior.
