from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import NamedTuple

from src.core.schemas import StatusOut


class AlertMessage(NamedTuple):
    key: str
    text: str


class AlertingService:
    def __init__(self, cooldown_sec: int) -> None:
        self.cooldown = timedelta(seconds=max(cooldown_sec, 0))
        self._last_sent_at: dict[str, datetime] = {}

    def collect_alert_messages(self, status: StatusOut) -> list[str]:
        return [item.text for item in self.collect_pending_alerts(status)]

    def collect_pending_alerts(self, status: StatusOut) -> list[AlertMessage]:
        now = datetime.now(timezone.utc)
        messages: list[AlertMessage] = []

        risk = status.risk
        for warning in risk.warnings:
            key = f"risk:{warning}"
            if self._is_due(key, now):
                messages.append(AlertMessage(key=key, text=f"RISK ALERT: {warning}"))

        for conn in status.connector_statuses:
            if conn.ok:
                continue
            key = f"connector:{conn.exchange}:{conn.error}"
            if self._is_due(key, now):
                messages.append(AlertMessage(key=key, text=f"CONNECTOR ALERT [{conn.exchange}]: {conn.error}"))

        return messages

    def mark_sent(self, key: str, sent_at: datetime | None = None) -> None:
        self._last_sent_at[key] = sent_at or datetime.now(timezone.utc)

    def _is_due(self, key: str, now: datetime) -> bool:
        prev = self._last_sent_at.get(key)
        if prev and now - prev < self.cooldown:
            return False
        return True
