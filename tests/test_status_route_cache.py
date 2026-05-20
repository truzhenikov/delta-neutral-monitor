from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from src.api import routes


@dataclass
class FakeMonitoring:
    calls: int = 0

    async def collect_with_status(self):
        self.calls += 1
        return [object()], [object()]


class FakeSnapshot:
    def __init__(self, marker: str) -> None:
        self.marker = marker

    def model_dump(self) -> dict:
        return {"marker": self.marker}


class FakeStatusService:
    def __init__(self) -> None:
        self.calls = 0

    def build_status(self, accounts, connector_statuses=None):
        self.calls += 1
        return FakeSnapshot(marker=f"snapshot-{self.calls}")


def test_status_route_reuses_recent_payload(monkeypatch) -> None:
    monitoring = FakeMonitoring()
    status_service = FakeStatusService()

    monkeypatch.setattr(routes, 'get_monitoring_service', lambda: monitoring)
    monkeypatch.setattr(routes, 'get_status_service', lambda: status_service)
    monkeypatch.setattr(routes, 'get_settings', lambda: type('Settings', (), {'status_cache_ttl_sec': 15.0})())
    monkeypatch.setattr(routes, '_status_cache_payload', None)
    monkeypatch.setattr(routes, '_status_cache_expires_at', 0.0)

    first = asyncio.run(routes.status())
    second = asyncio.run(routes.status())

    assert first == {"marker": "snapshot-1"}
    assert second == first
    assert monitoring.calls == 1
    assert status_service.calls == 1
