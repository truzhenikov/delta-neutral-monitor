# BingX USDJPY valuation units

## Sources and evidence

- [BingX Forex launch FAQ, question 5](https://bingx.com/en/support/articles/13628544052239): position value = filled amount × filled price × middle rate. For pairs not quoted in USDT, the quote-currency/USDT middle rate also applies to margin, trading fees, funding and PnL. This is not a fixed contract multiplier.
- [Official BingX API account reference](https://github.com/BingX-API/api-ai-skills/blob/main/skills/swap-account/api-reference.md), `GET /openApi/swap/v2/user/positions`: `positionValue` is native position notional; `unrealizedProfit` is native unrealized PnL; `currency` is settlement currency.
- Public `GET /openApi/swap/v2/quote/contracts` identifies `NCFXUSD2JPY-USDT` as `USDJPY-USDT`, `currency=USDT`, `asset=NCFXUSD2JPY`. Its `size=0.01` is not evidence of a valuation multiplier and is not used as one.

Sanitized authenticated example (no account identifiers): positionAmt 8519.98, avgPrice 159.366, markPrice 159.400, initialMargin 85.2338, leverage 100, positionValue 8525.16 USDT, unrealizedProfit 1.7901 USDT, liquidationPrice 100.522. Naive quantity × price gives an erroneous $1.36m. The native value is about $8.5k, consistent with margin × leverage and the opposing Ondo synthetic: short 53.51 × 159.322 ≈ $8.5k.

## Mapping

For this explicitly verified contract only, use native USDT notional and PnL (nominal USD, consistent with existing USDT accounts). Normalize displayed/matching `size = positionValue / markPrice`: **USD mark-equivalent synthetic units**, not physical USD base quantity, JPY units, exchange lots, or an executable order size. This is an economic valuation conversion, not a fitted multiplier. Carry native PnL independently; a middle rate can change and mark-equivalent size × price change does not reproduce native PnL exactly.

Keep native mark, entry and liquidation prices in their original quote-price units. Canonical position symbol is USDJPY-USDT; funding labels use the same verified identity even if public metadata is unavailable. No funding amount is rescaled. Consumers removing settlement suffixes can match this to Ondo USDJPY-USD.P using normalized size rather than native USD quantity.

Fail the account snapshot closed for other nonflat NCFX contracts until individually reconciled, unsupported settlement/side, or missing/nonfinite/invalid valuation inputs. Zero positions can be skipped without requiring a valid mark. Crypto and commodities (including NCCOXAG2USD-USDT's ounce quantity) retain their previous mapping. This change neither trades nor alters account configuration, liquidation thresholds, Telegram routing or schedules.
