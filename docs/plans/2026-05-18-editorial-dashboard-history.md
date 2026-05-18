# Editorial Dashboard + Portfolio History Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Ship a cleaner editorial/financial landing dashboard, preserve live warnings, and add persistent 4-hour portfolio history with chart + day-by-day historical warnings review.

**Architecture:** Extend the backend status pipeline with a persisted history repository stored on disk as JSON under `data/portfolio-history/`, then expose a new `/v1/history` API built from the same status/risk structures used by `/v1/status`. On the frontend, keep the existing landing/dashboard concept but restyle it into an editorial financial layout and add a history section that renders an inline SVG chart, historical warning snapshots, and a daily changes table from the new API payload.

**Tech Stack:** FastAPI, Pydantic, Python pytest, systemd, Next.js App Router, React 19, TypeScript, node:test, Vercel

---

### Task 1: Add backend regression tests for history persistence and API shape

**Objective:** Define the new history behavior before implementation.

**Files:**
- Modify: `tests/test_status_service.py`
- Modify: `tests/test_monitoring_service.py`
- Create: `tests/test_history_service.py`
- Create: `tests/test_api_history_route.py`

**Step 1: Write failing test**

```python
from datetime import datetime, timezone
from pathlib import Path

from src.core.models import AccountSnapshot, ConnectorStatus, Position
from src.core.risk import RiskEngine
from src.services.history_service import HistoryService
from src.services.status_service import StatusService


def test_build_status_includes_embedded_history_snapshot() -> None:
    updated_at = datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc)
    account = AccountSnapshot(
        exchange="bitget",
        equity_usd=1000.0,
        available_margin_usd=600.0,
        maintenance_margin_usd=120.0,
        updated_at=updated_at,
        positions=[],
    )
    connector_status = ConnectorStatus(
        exchange="bitget",
        ok=True,
        error=None,
        updated_at=updated_at,
    )
    service = StatusService(RiskEngine(0.75, 12.0, 500.0))

    status = service.build_status([account], [connector_status])

    assert status.current_snapshot.total_equity_usd == 1000.0
    assert status.current_snapshot.warning_count == len(status.risk.warnings)
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_status_service.py::test_build_status_includes_embedded_history_snapshot -v`
Expected: FAIL — `StatusOut` has no `current_snapshot`

**Step 3: Write minimal implementation**

Add copy-pasteable schema/service support in:

```python
class PortfolioHistorySnapshotOut(BaseModel):
    recorded_at: datetime
    total_equity_usd: float
    total_available_margin_usd: float
    total_maintenance_margin_usd: float
    warning_count: int
    warnings: list[str]
```

and embed it on `StatusOut` as `current_snapshot`.

**Step 4: Run test to verify pass**

Run: `pytest tests/test_status_service.py::test_build_status_includes_embedded_history_snapshot -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_status_service.py src/core/schemas.py src/services/status_service.py
git commit -m "test: define status snapshot payload for history"
```

---

### Task 2: Add history repository + retention window tests

**Objective:** Define how 4-hour snapshots are persisted and deduplicated.

**Files:**
- Create: `src/services/history_service.py`
- Create: `tests/test_history_service.py`
- Modify: `src/config.py`

**Step 1: Write failing test**

```python
def test_history_service_persists_one_snapshot_per_4h_bucket(tmp_path: Path) -> None:
    service = HistoryService(storage_dir=tmp_path, interval_hours=4, retention_days=30)

    first = datetime(2026, 5, 18, 8, 5, tzinfo=timezone.utc)
    second = datetime(2026, 5, 18, 11, 45, tzinfo=timezone.utc)

    service.record(snapshot=sample_snapshot(recorded_at=first))
    service.record(snapshot=sample_snapshot(recorded_at=second, total_equity_usd=999.0))

    history = service.read_history()

    assert len(history) == 1
    assert history[0].total_equity_usd == 999.0
    assert history[0].warnings == ["warning-a"]
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_history_service.py::test_history_service_persists_one_snapshot_per_4h_bucket -v`
Expected: FAIL — `HistoryService` missing

**Step 3: Write minimal implementation**

Create `src/services/history_service.py` with file-backed JSON storage, bucket normalization, and retention helpers like:

```python
class HistoryService:
    def __init__(self, storage_dir: Path, interval_hours: int = 4, retention_days: int = 30) -> None:
        self.storage_dir = storage_dir
        self.interval_hours = interval_hours
        self.retention_days = retention_days
        self.history_path = self.storage_dir / "portfolio-history.json"
```

and config defaults in `src/config.py`:

```python
history_storage_dir: str = Field(default="data/portfolio-history", alias="HISTORY_STORAGE_DIR")
history_snapshot_interval_hours: int = Field(default=4, alias="HISTORY_SNAPSHOT_INTERVAL_HOURS")
history_retention_days: int = Field(default=30, alias="HISTORY_RETENTION_DAYS")
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_history_service.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add tests/test_history_service.py src/services/history_service.py src/config.py
git commit -m "feat: add persistent portfolio history storage"
```

---

### Task 3: Persist current snapshots during status generation and expose `/v1/history`

**Objective:** Save warnings with snapshots and provide a history API for the frontend.

**Files:**
- Modify: `src/deps.py`
- Modify: `src/services/status_service.py`
- Modify: `src/api/routes.py`
- Modify: `src/core/schemas.py`
- Create: `tests/test_api_history_route.py`

**Step 1: Write failing test**

```python
from fastapi.testclient import TestClient
from src.main import app


def test_history_route_returns_chart_and_daily_rows(monkeypatch) -> None:
    client = TestClient(app)
    response = client.get('/v1/history')

    assert response.status_code == 200
    payload = response.json()
    assert 'snapshots' in payload
    assert 'chart' in payload
    assert 'daily_changes' in payload
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_api_history_route.py::test_history_route_returns_chart_and_daily_rows -v`
Expected: FAIL — `/v1/history` not found

**Step 3: Write minimal implementation**

Add route and response models such as:

```python
@router.get("/v1/history")
async def history() -> dict:
    history_service = get_history_service()
    return history_service.build_history_response().model_dump()
```

Also update `StatusService` so `/v1/status` records the newly built current snapshot into `HistoryService`.

**Step 4: Run test to verify pass**

Run: `pytest tests/test_api_history_route.py::test_history_route_returns_chart_and_daily_rows -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/deps.py src/services/status_service.py src/api/routes.py src/core/schemas.py tests/test_api_history_route.py
git commit -m "feat: expose portfolio history api"
```

---

### Task 4: Add a production-safe 4-hour snapshot trigger

**Objective:** Ensure history snapshots are written in the running system every 4 hours.

**Files:**
- Create: `src/scripts/capture_history_snapshot.py`
- Create: `deploy/delta-neutral-monitor-history.service`
- Create: `deploy/delta-neutral-monitor-history.timer`
- Create: `tests/test_capture_history_snapshot.py`

**Step 1: Write failing test**

```python
def test_capture_history_snapshot_runs_collection_and_persists(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv('HISTORY_STORAGE_DIR', str(tmp_path))

    exit_code = main()

    assert exit_code == 0
    assert (tmp_path / 'portfolio-history.json').exists()
```

**Step 2: Run test to verify failure**

Run: `pytest tests/test_capture_history_snapshot.py::test_capture_history_snapshot_runs_collection_and_persists -v`
Expected: FAIL — capture script missing

**Step 3: Write minimal implementation**

Create a script that calls the same monitor/status path used by `/v1/status`, then ship systemd units:

```ini
[Timer]
OnCalendar=*-*-* 00/4:00:00
Persistent=true
Unit=delta-neutral-monitor-history.service
```

**Step 4: Run test to verify pass**

Run: `pytest tests/test_capture_history_snapshot.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/scripts/capture_history_snapshot.py deploy/delta-neutral-monitor-history.service deploy/delta-neutral-monitor-history.timer tests/test_capture_history_snapshot.py
git commit -m "feat: schedule 4h portfolio history snapshots"
```

---

### Task 5: Add frontend history data contracts and logic tests

**Objective:** Define the chart and day-by-day table transformations before UI work.

**Files:**
- Modify: `webapp/lib/types.ts`
- Create: `webapp/lib/history.ts`
- Create: `webapp/lib/history.test.ts`
- Modify: `webapp/lib/api.ts`

**Step 1: Write failing test**

```ts
import test from 'node:test'
import * as assert from 'node:assert/strict'

import { buildDailyHistoryRows, buildHistoryChartPoints } from './history'

test('buildDailyHistoryRows groups latest snapshot per day and carries warnings', () => {
  const rows = buildDailyHistoryRows(sampleHistory)
  assert.equal(rows[0]?.warningCount, 2)
  assert.match(rows[0]?.warnings[0] ?? '', /Margin ratio/)
})
```

**Step 2: Run test to verify failure**

Run: `npm test -- --test-name-pattern="buildDailyHistoryRows"`
Expected: FAIL — module missing

**Step 3: Write minimal implementation**

Add history types and helper functions like:

```ts
export type HistorySnapshot = {
  recorded_at: string
  total_equity_usd: number
  total_available_margin_usd: number
  total_maintenance_margin_usd: number
  warning_count: number
  warnings: string[]
}
```

**Step 4: Run test to verify pass**

Run: `npm test -- --test-name-pattern="buildDailyHistoryRows"`
Expected: PASS

**Step 5: Commit**

```bash
git add webapp/lib/types.ts webapp/lib/history.ts webapp/lib/history.test.ts webapp/lib/api.ts
git commit -m "test: define frontend history transformations"
```

---

### Task 6: Redesign the landing/dashboard shell into the editorial financial style

**Objective:** Replace the current dark “AI style” shell with the new cream/serif/editorial visual system while preserving live warnings.

**Files:**
- Modify: `webapp/components/dashboard-shell.tsx`
- Modify: `webapp/components/summary-cards.tsx`
- Modify: `webapp/components/summary-card.tsx`
- Modify: `webapp/components/compact-exchange-overview.tsx`
- Modify: `webapp/components/exchange-card.tsx`
- Modify: `webapp/components/positions-table.tsx`
- Modify: `webapp/app/globals.css`

**Step 1: Write failing test**

```ts
test('dashboard source preserves warnings section copy in the landing shell', async () => {
  const source = await readFile('components/dashboard-shell.tsx', 'utf8')
  assert.match(source, /Warnings/)
  assert.match(source, /history/i)
})
```

**Step 2: Run test to verify failure**

Run: `npm test -- --test-name-pattern="dashboard source preserves warnings section copy"`
Expected: FAIL — new history/editorial markers absent

**Step 3: Write minimal implementation**

Update the shell to include:
- cream/beige background
- serif hero/section titles
- pale pink primary button
- thin divider sections
- status pills
- warning panel kept prominent
- overview laid out as table-like editorial rows

**Step 4: Run test to verify pass**

Run: `npm test -- --test-name-pattern="dashboard source preserves warnings section copy"`
Expected: PASS

**Step 5: Commit**

```bash
git add webapp/components/dashboard-shell.tsx webapp/components/summary-cards.tsx webapp/components/summary-card.tsx webapp/components/compact-exchange-overview.tsx webapp/components/exchange-card.tsx webapp/components/positions-table.tsx webapp/app/globals.css
git commit -m "feat: redesign dashboard with editorial financial layout"
```

---

### Task 7: Render history chart, historical warnings, and day-by-day change table

**Objective:** Make persisted portfolio history visible in the UI.

**Files:**
- Modify: `webapp/components/dashboard-shell.tsx`
- Create: `webapp/components/history-chart.tsx`
- Create: `webapp/components/history-table.tsx`
- Create: `webapp/components/history-warning-log.tsx`
- Modify: `webapp/app/api/status/demo-status.json`
- Create: `webapp/app/api/status/demo-history.json`

**Step 1: Write failing test**

```ts
test('history chart points cover all snapshots and change rows expose day deltas', () => {
  const chart = buildHistoryChartPoints(sampleHistory.snapshots)
  const rows = buildDailyHistoryRows(sampleHistory)
  assert.equal(chart.length, sampleHistory.snapshots.length)
  assert.ok(rows[0]?.changeUsd !== undefined)
})
```

**Step 2: Run test to verify failure**

Run: `npm test -- --test-name-pattern="history chart points cover all snapshots"`
Expected: FAIL — UI/data path incomplete

**Step 3: Write minimal implementation**

Create components that render:
- inline SVG equity chart
- latest snapshot list with warning pills
- daily table columns: date, close equity, day change, warning count, warnings summary

**Step 4: Run test to verify pass**

Run: `npm test -- --run`
Expected: PASS

**Step 5: Commit**

```bash
git add webapp/components/history-chart.tsx webapp/components/history-table.tsx webapp/components/history-warning-log.tsx webapp/components/dashboard-shell.tsx webapp/app/api/status/demo-history.json webapp/app/api/status/demo-status.json
git commit -m "feat: add portfolio history dashboard section"
```

---

### Task 8: Full verification, deploy, restart, and save the plan commit

**Objective:** Validate backend/frontend, push to GitHub, deploy to Vercel, restart backend services, and smoke-test live URLs.

**Files:**
- Modify if needed: `docs/plans/2026-05-18-editorial-dashboard-history.md`

**Step 1: Run backend tests**

```bash
pytest tests/ -q
```

Expected: PASS

**Step 2: Run frontend tests and build**

```bash
cd webapp && npm test -- --run
cd webapp && npx tsc --noEmit
cd webapp && npm run build
```

Expected: PASS

**Step 3: Deploy/restart**

```bash
sudo systemctl daemon-reload
sudo systemctl restart delta-neutral-monitor-backend
sudo systemctl enable --now delta-neutral-monitor-history.timer
npx --yes vercel deploy --prod --yes --token="$VERCEL_TOKEN"
```

Expected: backend healthy, timer active, Vercel deploy ready

**Step 4: Verify live behavior**

```bash
python - <<'PY'
import urllib.request
for url in [
    'http://127.0.0.1:8080/health',
    'http://127.0.0.1:8080/v1/status',
    'http://127.0.0.1:8080/v1/history',
]:
    with urllib.request.urlopen(url, timeout=20) as response:
        print(url, response.status)
PY
```

Then smoke-test `https://webapp-psi-ruby.vercel.app` in the browser snapshot + console.

**Step 5: Commit and push**

```bash
git add docs/plans/2026-05-18-editorial-dashboard-history.md
git commit -m "docs: add editorial dashboard history implementation plan"
git push origin main
```
