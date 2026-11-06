# Hyperliquid HIP-3 namespace discovery

## Coverage and configuration

A successful account snapshot must cover the entire selected public account, not just the DEXs named in an old environment/profile list. The connector now queries `POST /info` with `{"type":"perpDexs"}` on every snapshot. The documented registry shape is a list whose first entry is `null` (native DEX), followed by builder DEX objects containing `name`. See the [official Python SDK](https://github.com/hyperliquid-dex/hyperliquid-python-sdk/blob/master/hyperliquid/info.py), `perp_dexs`, its constructor's native-Dex handling, and `user_state`.

The ordered union is:

1. Native perp DEX (`""`, represented by omitting `dex` in account/metadata requests).
2. Explicit comma-separated extras from nonempty profile `dex`, otherwise `HYPERLIQUID_DEX` (same precedence as before).
3. Every registered HIP-3 name.

Configuration is now additive, **not a namespace restriction**. Explicit extras absent from the registry are still queried; their errors cannot be silently ignored. Names are deduplicated before fetching and summing balances. Empty configuration is valid and does not turn discovery off. There is no registry cache or fallback to a stale configured subset on failure.

Each distinct namespace contributes its clearinghouse positions and ordinary per-DEX financial fields once. Full native coin identifiers survive unchanged: `xyz:UNITREE-PERP` and `para:UNITREE-PERP` remain distinct positions. The synthetic incident regression has xyz long 41, para long 10 and Lighter short 51 at the same mark, producing zero combined quantity/value delta. Existing risk evaluation sums every position rather than deduplicating by ticker. It does not provide a separate per-underlying grouping model.

## Fail-closed collection and latency

Discovery transport errors, malformed/empty discovery, malformed clearinghouse/metadata envelopes, or a failed required DEX request abort the whole live snapshot. The collector reports failure and can retain an older cached snapshot with failed status; it must not label a new partial account successful. Requests belonging to a failed or cancelled snapshot are cancelled and drained.

Budget checked before implementation: runtime HTTP timeout is 10 seconds, aggregate `MonitoringService` connector deadline 30 seconds, bot status timeout 45 seconds (all independently configurable). Extending the previous serial `2N + 3` request loop to all registered DEXs would consume that deadline unnecessarily. All snapshot reads now share a semaphore capped at **four concurrent HTTP requests**. Spot reads run alongside DEX reads. Per-DEX `metaAndAssetCtxs` is fetched only when that DEX has position rows; flat DEX clearinghouse balances are still included. Request count is one discovery + one state per distinct namespace + one metadata request per populated namespace + three spot reads.

The global deadline is unchanged and remains authoritative: sufficiently slow upstream reads can still time out, correctly failing rather than returning a partial account. This is not a promise that every possible registry size can be fully scanned within 30 seconds.

## Equity and availability

Financial mapping is unchanged, with coverage extended to all DEXs:

- Ordinary accounts sum distinct per-DEX account values, maintenance and withdrawable/fallback availability.
- Positive spot portfolio valuation replaces perps equity; it is not added to it.
- Explicit portfolio-margin accounts replace per-DEX availability with token-0 `tokenToAvailableAfterMaintenance`; spot availability is not multiplied by the DEX count.
- Missing/malformed native portfolio availability remains an error, not known zero.

See [portfolio-margin field semantics and limitations](hyperliquid-portfolio-margin.md). No account mode, configuration/credentials, enablement, margin, transfer or trading mutation is performed by discovery.

## Funding history scope

The repository's `FundingReportService._hyperliquid_rows` does **not** iterate configured DEXs. It queries the account-wide `userFunding` ledger by public address and time window, matching the official SDK's `user_funding_history` request (which has no `dex` field). Thus a second namespace loop would be inappropriate and could duplicate account-wide payments. No production funding change is needed for this incident. The funding regression now explicitly accepts simultaneous `xyz:UNITREE` and `para:UNITREE` records and sums them into normalized `UNITREE` without losing either native record. Namespace omission in any separate, external funding script is outside this repository fix and needs its own inspection; no claim about external collectors is made here.

## Verification

```sh
python3 -m pytest tests/test_hyperliquid_dex_discovery.py tests/test_hyperliquid_connector.py tests/test_hyperliquid_portfolio_available.py tests/test_funding_report_service.py -q
```

Initial RED run of `test_hyperliquid_dex_discovery.py`: **16 failed**. The primary assertion reproduced the missing para leg: only `xyz:UNITREE-PERP` long 41 instead of both xyz 41 and para 10. Additional failing cases exercised duplicate configuration, registry errors, required DEX failure, malformed state and serial latency. Tests use synthetic identities and responses; live account verification and service deployment are separate steps.
