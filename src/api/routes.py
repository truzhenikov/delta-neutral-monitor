from __future__ import annotations

import asyncio
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from src.config import get_settings
from src.deps import get_history_service, get_monitoring_service, get_status_service

router = APIRouter()
_status_cache_lock = asyncio.Lock()
_status_cache_payload: dict | None = None
_status_cache_expires_at = 0.0


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/v1/status")
async def status() -> dict:
    global _status_cache_payload, _status_cache_expires_at

    now = time.monotonic()
    if _status_cache_payload is not None and now < _status_cache_expires_at:
        return _status_cache_payload

    async with _status_cache_lock:
        now = time.monotonic()
        if _status_cache_payload is not None and now < _status_cache_expires_at:
            return _status_cache_payload

        monitoring = get_monitoring_service()
        status_service = get_status_service()
        accounts, connector_statuses = await monitoring.collect_with_status()
        snapshot = status_service.build_status(accounts, connector_statuses=connector_statuses)
        payload = snapshot.model_dump()
        _status_cache_payload = payload
        _status_cache_expires_at = now + get_settings().status_cache_ttl_sec
        return payload


@router.get("/v1/history")
async def history() -> dict:
    history_service = get_history_service()
    return history_service.build_history_response().model_dump()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard() -> str:
    path = Path(__file__).resolve().parents[1] / "web" / "dashboard.html"
    return path.read_text(encoding="utf-8")
