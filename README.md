# Delta Neutral Monitor

Delta Neutral Monitor is a self-hosted monitoring stack for multi-venue futures portfolios.
It aggregates balances, margin usage, positions, risk warnings, and portfolio history across supported exchanges and can expose the data through:

- a FastAPI backend (`/health`, `/v1/status`, `/v1/history`)
- a Next.js dashboard (`webapp/`)
- an optional Telegram bot for alerts and operational workflows

## What this project does

The monitor is designed for **real exchange data**, not for screenshots or static demos.
For every configured venue it normalizes account state into a common shape and calculates:

- total portfolio equity
- available / maintenance margin
- per-position notional, PnL, and delta
- portfolio-level risk warnings
- historical equity snapshots

### Supported integrations

Current runtime integrations in this repo target futures accounts for:

- Bitget
- BingX
- Hyperliquid
- Extended
- OKX
- KuCoin Futures
- ADEN

## No demo mode

This project no longer ships a bundled web demo fallback.

**Current behavior:**
- if connectors are healthy, the API returns `source: "live"`
- if one or more connectors fail during refresh, the backend reuses the last cached account snapshot for those venues and returns `source: "stale"`
- the response also includes `snapshot_updated_at`, so the UI can clearly show how old the reused snapshot is
- the dashboard marks stale venues and keeps showing the last successfully loaded snapshot instead of swapping to fake/demo numbers

This is important for production use: **real stale data is always better than fake fresh-looking demo data**.

## Repository layout

```text
src/                 FastAPI app, connectors, monitoring, Telegram bot
webapp/              Next.js dashboard for VPS hosting or optional Vercel frontend
deploy/              Example systemd service units
tests/               Backend tests
docs/                Additional notes and deployment docs
```

## Quick start

### 1) Clone and install backend dependencies

```bash
git clone https://github.com/truzhenikov/delta-neutral-monitor.git
cd delta-neutral-monitor
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2) Configure environment

```bash
cp .env.example .env
```

Fill in the exchange credentials you actually use. At minimum, configure:

- `ENABLED_EXCHANGES`
- the API credentials for those exchanges
- optionally Telegram variables if you want alerts/bot control

> The sample file intentionally does **not** enable any demo/mock runtime mode.

### 3) Run the backend

```bash
uvicorn src.main:app --host 0.0.0.0 --port 8080 --reload
```

Verify:

```bash
curl http://127.0.0.1:8080/health
curl http://127.0.0.1:8080/v1/status
curl http://127.0.0.1:8080/v1/history
```

### 4) Run the web dashboard

```bash
cd webapp
npm install
MONITOR_API_BASE_URL=http://127.0.0.1:8080 npm run dev
```

Open `http://localhost:3000`.

### 5) Optional: run the Telegram bot

From the repo root:

```bash
python -m src.bot.run
```

## API contract

### `GET /health`
Basic liveness probe.

### `GET /v1/status`
Returns the current portfolio state.

Important fields:

- `accounts[]` — normalized exchange-level snapshots
- `connector_statuses[]` — connector health for each exchange
- `risk` — portfolio-level warnings and metrics
- `current_snapshot` — current aggregate snapshot object
- `source` — `live` or `stale`
- `snapshot_updated_at` — oldest timestamp among the account snapshots used in the current response

### `GET /v1/history`
Returns persisted portfolio history for the dashboard chart/table.

## Stale-data behavior

The backend persists the latest successful per-exchange account snapshots under `HISTORY_STORAGE_DIR`.

If an exchange fails during refresh:

1. the connector is marked `ok=false`
2. the latest cached account snapshot for that exchange is reused if available
3. the overall response is marked `source="stale"`
4. the dashboard highlights stale exchanges and shows the snapshot timestamp

History recording is intentionally conservative:

- new history points are persisted only when **all** connector statuses are healthy
- mixed live+stale refreshes are visible in the current dashboard, but are not written as canonical history points

## Production deployment overview

Recommended production topology:

- **backend on a VPS** under `systemd`
- **Next.js webapp on the same VPS** under `systemd`
- **public nginx reverse proxy** in front of both services
- optional **Vercel** only as a secondary frontend, not as the primary production path

Use the sample deploy files in `deploy/`:

- `deploy/delta-neutral-monitor-backend.service`
- `deploy/delta-neutral-monitor-history.service`
- `deploy/delta-neutral-monitor-webapp.service`
- `deploy/delta-neutral-monitor-bot.service`
- `deploy/delta-neutral-monitor.nginx.conf`

A detailed production walkthrough is in:

- [`docs/self-hosting.md`](docs/self-hosting.md)

## Frontend deployment (self-hosted recommended)

Recommended production path:

- run `webapp/` on the same VPS via `deploy/delta-neutral-monitor-webapp.service`
- proxy `/` and `/api/` through nginx to `127.0.0.1:3000`
- proxy `/health` and `/v1/` through nginx to `127.0.0.1:8080`

The Next.js API routes in `webapp/app/api/*` are strict proxies:

- they return live/stale backend data when the backend responds
- they return `502` with an error payload when the backend is unavailable
- they do **not** inject demo payloads

If you still keep a Vercel frontend for convenience, treat it as optional and point `MONITOR_API_BASE_URL` at a stable VPS origin rather than a temporary tunnel.

## Tests

### Backend

```bash
pytest -q
```

### Frontend logic/source tests

```bash
cd webapp
npm test
```

## Common operational notes

- If a venue starts failing because of IP whitelist restrictions or temporary upstream errors, the UI will keep rendering the last cached snapshot for that venue and label it stale.
- If the dashboard unexpectedly shows no data in production, first check the frontend env var `MONITOR_API_BASE_URL`.
- If the backend is reachable locally but not publicly, fix the VPS network/reverse-proxy layer instead of reintroducing a fake fallback.

## Status of the project

This repo is optimized for practical operations:

- real exchange connectors
- explicit stale-data semantics
- deployable backend/frontend split
- self-hostable configuration

If you want to share this project with other users, point them at:

1. this `README.md`
2. `.env.example`
3. `docs/self-hosting.md`

That gives them the install path, runtime model, and production deployment shape without depending on any hidden demo mode.
