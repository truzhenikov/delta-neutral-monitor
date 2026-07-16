from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, NamedTuple

from src.core.schemas import StatusOut

if TYPE_CHECKING:
    from src.services.credential_store import CredentialStore


class AlertMessage(NamedTuple):
    key: str
    text: str


class AlertingService:
    def __init__(self, cooldown_sec: int) -> None:
        self.cooldown = timedelta(seconds=max(cooldown_sec, 0))
        self._last_sent_at: dict[str, datetime] = {}
        self._sticky_sent_keys: set[str] = set()

    def collect_alert_messages(self, status: StatusOut) -> list[str]:
        return [item.text for item in self.collect_pending_alerts(status)]

    def collect_pending_alerts(self, status: StatusOut) -> list[AlertMessage]:
        return self.collect_pending_alerts_for_liq_threshold(status, min_liq_distance_pct=None)

    def collect_pending_alerts_for_liq_threshold(
        self,
        status: StatusOut,
        min_liq_distance_pct: float | None,
    ) -> list[AlertMessage]:
        now = datetime.now(timezone.utc)
        messages: list[AlertMessage] = []

        for warning in self._non_liq_risk_warnings(status.risk.warnings):
            key = f"risk:{warning}"
            if self._is_due(key, now):
                messages.append(AlertMessage(key=key, text=f"RISK ALERT: {warning}"))

        for warning in self._build_liq_distance_warnings(status, min_liq_distance_pct):
            key = f"risk:{warning}"
            if self._is_due(key, now):
                messages.append(AlertMessage(key=key, text=f"RISK ALERT: {warning}"))

        for conn in status.connector_statuses:
            if conn.ok:
                continue
            error_text = (conn.error or "").strip()
            if self._should_skip_connector_alert(error_text):
                continue
            key = f"connector:{conn.exchange}:{error_text}"
            if self._is_due(key, now):
                messages.append(AlertMessage(key=key, text=f"CONNECTOR ALERT [{conn.exchange}]: {error_text}"))

        return messages

    def collect_pending_token_expiry_alerts(
        self,
        credential_store: CredentialStore,
        *,
        now: datetime | None = None,
        warning_window: timedelta = timedelta(hours=24),
    ) -> list[AlertMessage]:
        effective_now = now or datetime.now(timezone.utc)
        messages: list[AlertMessage] = []

        for entry in credential_store.list_configured_exchanges():
            if entry.get("base_exchange") != "variational" or not entry.get("enabled"):
                continue
            exchange_ref = str(entry.get("exchange") or "").strip().lower()
            raw_token = credential_store.get_exchange_credentials(exchange_ref).get("vr_token", "")
            expires_at = self._decode_jwt_expiry(raw_token)
            if expires_at is None:
                continue

            exp_ts = int(expires_at.timestamp())
            exp_utc = expires_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            remaining = expires_at - effective_now
            expiring_key = f"token:{exchange_ref}:expiring:{exp_ts}"
            expired_key = f"token:{exchange_ref}:expired:{exp_ts}"

            if remaining <= timedelta(0):
                self._sticky_sent_keys.discard(expiring_key)
                if expired_key not in self._sticky_sent_keys:
                    messages.append(
                        AlertMessage(
                            key=expired_key,
                            text=f"TOKEN EXPIRED [{exchange_ref}]: Variational vr-token expired at {exp_utc}. Update the token in the bot.",
                        )
                    )
                continue

            self._sticky_sent_keys.discard(expired_key)
            if remaining <= warning_window and expiring_key not in self._sticky_sent_keys:
                hours_left = int(remaining.total_seconds() // 3600)
                minutes_left = int((remaining.total_seconds() % 3600) // 60)
                messages.append(
                    AlertMessage(
                        key=expiring_key,
                        text=(
                            f"TOKEN EXPIRING [{exchange_ref}]: Variational vr-token expires at {exp_utc} "
                            f"(in {hours_left}h {minutes_left}m)."
                        ),
                    )
                )

        return messages

    def _non_liq_risk_warnings(self, warnings: list[str]) -> list[str]:
        return [warning for warning in warnings if self._parse_liq_warning_threshold(warning) is None]

    def _build_liq_distance_warnings(self, status: StatusOut, min_liq_distance_pct: float | None) -> list[str]:
        threshold = float(min_liq_distance_pct) if min_liq_distance_pct is not None else self._default_liq_threshold(status)
        warnings: list[tuple[float, str]] = []
        for account in status.accounts:
            for position in account.positions:
                if position.liquidation_price is None or position.mark_price <= 0:
                    continue
                if position.side == "long":
                    dist = (position.mark_price - position.liquidation_price) / position.mark_price * 100
                else:
                    dist = (position.liquidation_price - position.mark_price) / position.mark_price * 100
                if dist <= threshold:
                    context = f"{position.exchange} {position.symbol} ({position.side})"
                    warnings.append((dist, context))
        if warnings:
            return [
                f"Min liquidation distance {dist:.2f}% <= threshold {threshold:.2f}% for {context}"
                for dist, context in sorted(warnings, key=lambda item: item[0])
            ]
        fallback_warnings: list[str] = []
        for warning in status.risk.warnings:
            parsed_threshold = self._parse_liq_warning_threshold(warning)
            if parsed_threshold is None:
                continue
            if status.risk.min_liq_distance_pct is not None and float(status.risk.min_liq_distance_pct) <= threshold:
                fallback_warnings.append(self._rewrite_liq_warning_threshold(warning, threshold))
        return fallback_warnings

    def _default_liq_threshold(self, status: StatusOut) -> float:
        thresholds = [
            value for value in (self._parse_liq_warning_threshold(warning) for warning in status.risk.warnings) if value is not None
        ]
        if thresholds:
            return thresholds[0]
        if status.risk.min_liq_distance_pct is not None:
            return float(status.risk.min_liq_distance_pct)
        return 12.0

    def _parse_liq_warning_threshold(self, warning: str) -> float | None:
        match = re.search(r"Min liquidation distance .* <= threshold ([0-9]+(?:\.[0-9]+)?)%", warning)
        if match is None:
            return None
        return float(match.group(1))

    def _rewrite_liq_warning_threshold(self, warning: str, threshold: float) -> str:
        return re.sub(
            r"(<= threshold )([0-9]+(?:\.[0-9]+)?)(%)",
            lambda match: f"{match.group(1)}{threshold:.2f}{match.group(3)}",
            warning,
            count=1,
        )

    def _should_skip_connector_alert(self, error_text: str) -> bool:
        normalized = error_text.lower()
        return "not configured" in normalized or "credentials are not configured" in normalized

    @staticmethod
    def _decode_jwt_expiry(raw_token: str) -> datetime | None:
        try:
            parts = raw_token.split(".")
            if len(parts) < 2:
                return None
            payload = parts[1] + ("=" * (-len(parts[1]) % 4))
            decoded = json.loads(base64.urlsafe_b64decode(payload).decode("utf-8"))
            exp = int(decoded.get("exp"))
            return datetime.fromtimestamp(exp, tz=timezone.utc)
        except Exception:
            return None

    def mark_sent(self, key: str, sent_at: datetime | None = None) -> None:
        self._last_sent_at[key] = sent_at or datetime.now(timezone.utc)
        if key.startswith("token:"):
            self._sticky_sent_keys.add(key)

    def _is_due(self, key: str, now: datetime) -> bool:
        prev = self._last_sent_at.get(key)
        if prev and now - prev < self.cooldown:
            return False
        return True
