# Delta Neutral Monitor (MVP)

TG bot + backend API for monitoring balances, margin, delta-neutral positions and liquidation risk across:
- Bitget
- BingX
- MEXC
- Hyperliquid
- Extended
- OKX
- KuCoin Futures

## Quick start

1. Install dependencies:

```bash
pip install -e .
```

2. Copy env:

```bash
cp .env.example .env
```

3. Run API:

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload
```

4. Run Telegram bot:

```bash
python -m src.bot.run
```

## API
- `GET /health`
- `GET /v1/status` aggregated account + risk snapshot
- `GET /dashboard` minimal web dashboard

## Notes
- `USE_MOCK_DATA=true` uses internal mock snapshots for all exchanges.
- `USE_MOCK_DATA=false` enables real connectors.
- Optional `TELEGRAM_ALERT_CHAT_ID` enables automatic push alerts with cooldown.
- Real connectors implemented now: Bitget, OKX, Hyperliquid, KuCoin Futures.
- BingX, MEXC, Extended are scaffolded and will return "not configured" until their auth/parsing is added.
