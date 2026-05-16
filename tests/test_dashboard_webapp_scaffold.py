from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBAPP = ROOT / "webapp"


def test_webapp_scaffold_files_exist() -> None:
    assert (WEBAPP / "package.json").exists()
    assert (WEBAPP / "app" / "page.tsx").exists()
    assert (WEBAPP / "app" / "api" / "status" / "route.ts").exists()


def test_demo_status_payload_has_accounts_and_totals() -> None:
    payload_path = WEBAPP / "app" / "api" / "status" / "demo-status.json"
    assert payload_path.exists()

    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    assert payload["accounts"]
    assert payload["total_equity_usd"] > 0
