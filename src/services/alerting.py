from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.core.schemas import StatusOut


class AlertingService:
    def __init__(self, cooldown_sec: int) -> None:
        self.cooldown = timedelta(seconds=max(cooldown_sec, 0))
        self._last_sent_at: dict[str, datetime] = {}

    def collect_alert_messages(self, status: StatusOut) -> list[str]:
        now = datetime.now(timezone.utc)
        messages: list[str] = []

        risk = status.risk
        for warning in risk.warnings:
            key = f"risk:{warning}"
            if self._can_send(key, now):
                messages.append(f"RISK ALERT: {warning}")

        for conn in status.connector_statuses:
            if conn.ok:
                continue
            key = f"connector:{conn.exchange}:{conn.error}"
            if self._can_send(key, now):
                messages.append(f"CONNECTOR ALERT [{conn.exchange}]: {conn.error}")

        return messages

    def _can_send(self, key: str, now: datetime) -> bool:
        prev = self._last_sent_at.get(key)
        if prev and now - prev < self.cooldown:
            return False
        self._last_sent_at[key] = now
        return True
