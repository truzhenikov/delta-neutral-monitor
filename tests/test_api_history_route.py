from __future__ import annotations

from datetime import datetime, timezone

from fastapi.testclient import TestClient

from src.api import routes
from src.core.schemas import HistoryChartPointOut, PortfolioHistoryOut, PortfolioHistorySnapshotOut, PortfolioHistorySummaryOut
from src.main import app


class StubHistoryService:
    def build_history_response(self) -> PortfolioHistoryOut:
        snapshot = PortfolioHistorySnapshotOut(
            recorded_at=datetime(2026, 5, 18, 0, 0, tzinfo=timezone.utc),
            total_equity_usd=1000.0,
            total_available_margin_usd=600.0,
            total_maintenance_margin_usd=120.0,
            warning_count=1,
            warnings=["Margin ratio warning"],
        )
        return PortfolioHistoryOut(
            snapshots=[snapshot],
            chart=[HistoryChartPointOut(label="2026-05-18 00:00", equity_usd=1000.0, recorded_at=snapshot.recorded_at)],
            daily_changes=[PortfolioHistorySummaryOut(date="2026-05-18", equity_usd=1000.0, change_usd=None, warning_count=1, warnings=["Margin ratio warning"])],
        )


def test_history_route_returns_chart_and_daily_rows(monkeypatch) -> None:
    monkeypatch.setattr(routes, "get_history_service", lambda: StubHistoryService())

    client = TestClient(app)
    response = client.get('/v1/history')

    assert response.status_code == 200
    payload = response.json()
    assert 'snapshots' in payload
    assert 'chart' in payload
    assert 'daily_changes' in payload
    assert payload['daily_changes'][0]['warning_count'] == 1
