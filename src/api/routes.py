from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from src.deps import get_history_service, get_monitoring_service, get_status_service

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/status")
async def status() -> dict:
    monitoring = get_monitoring_service()
    status_service = get_status_service()
    accounts, connector_statuses = await monitoring.collect_with_status()
    snapshot = status_service.build_status(accounts, connector_statuses=connector_statuses)
    return snapshot.model_dump()


@router.get("/v1/history")
async def history() -> dict:
    history_service = get_history_service()
    return history_service.build_history_response().model_dump()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    path = Path(__file__).resolve().parents[1] / "web" / "dashboard.html"
    return path.read_text(encoding="utf-8")
