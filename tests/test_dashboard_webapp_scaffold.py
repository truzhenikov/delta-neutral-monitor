from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEBAPP = ROOT / "webapp"


def test_webapp_scaffold_files_exist() -> None:
    assert (WEBAPP / "package.json").exists()
    assert (WEBAPP / "app" / "page.tsx").exists()
    assert (WEBAPP / "app" / "api" / "status" / "route.ts").exists()


def test_webapp_runtime_does_not_ship_demo_payload_files() -> None:
    assert not (WEBAPP / "app" / "api" / "status" / "demo-status.json").exists()
    assert not (WEBAPP / "app" / "api" / "history" / "demo-history.json").exists()
