from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx

from src.services.bingx_funding import fetch_bingx_funding
from src.services.credential_store import CredentialStore
from src.services.okx_funding import fetch_okx_funding
from src.services.variational_funding import fetch_variational_funding

MSK = ZoneInfo("Europe/Moscow")
_BOUNDARY_TIME = time(11, 30)
_RequestJson = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class FundingRow:
    exchange: str
    symbol: str
    amount_usd: Decimal


@dataclass(frozen=True)
class FundingReport:
    start: datetime
    end: datetime
    rows: list[FundingRow]
    warnings: list[str]
    complete: bool = True

    @property
    def total_usd(self) -> Decimal:
        return sum((row.amount_usd for row in self.rows), Decimal("0"))


class FundingReportService:
    def __init__(
        self,
        credential_store: CredentialStore,
        state_path: Path,
        *,
        hyperliquid_api_base: str = "https://api.hyperliquid.xyz",
        lighter_api_base: str = "https://mainnet.zklighter.elliot.ai",
        variational_api_base: str = "https://omni.variational.io",
        bingx_api_base: str = "https://open-api.bingx.com",
        okx_api_base: str = "https://www.okx.com",
        request_timeout_sec: float = 20.0,
        max_lighter_sample_gap_sec: int = 900,
        request_json: _RequestJson | None = None,
    ) -> None:
        self.credential_store = credential_store
        self.state_path = state_path
        self.hyperliquid_api_base = hyperliquid_api_base.rstrip("/")
        self.lighter_api_base = lighter_api_base.rstrip("/")
        self.variational_api_base = variational_api_base.rstrip("/")
        self.bingx_api_base = bingx_api_base.rstrip("/")
        self.okx_api_base = okx_api_base.rstrip("/")
        self.request_timeout_sec = request_timeout_sec
        self.max_lighter_sample_gap_sec = max_lighter_sample_gap_sec
        self._request_json_override = request_json

    @staticmethod
    def completed_window(now: datetime) -> tuple[datetime, datetime]:
        local_now = _as_msk(now)
        today_boundary = datetime.combine(local_now.date(), _BOUNDARY_TIME, tzinfo=MSK)
        end = today_boundary if local_now >= today_boundary else today_boundary - timedelta(days=1)
        return end - timedelta(days=1), end

    @staticmethod
    def _window_end_for_observation(now: datetime) -> datetime:
        local_now = _as_msk(now)
        # Lighter settles funding on the hour. Attribute a newly observed
        # cumulative change to that hour, including the 11:00 settlement in
        # the reporting window that closes at 11:30.
        event_hour = local_now.replace(minute=0, second=0, microsecond=0)
        boundary = datetime.combine(local_now.date(), _BOUNDARY_TIME, tzinfo=MSK)
        return boundary if event_hour < boundary else boundary + timedelta(days=1)

    async def capture_lighter_sample(self, now: datetime | None = None) -> None:
        effective_now = _as_msk(now or datetime.now(tz=MSK))
        values: dict[str, Decimal] = {}
        errors: list[str] = []
        seen_accounts: set[str] = set()

        for exchange_ref in self._enabled_refs("lighter"):
            credentials = self.credential_store.get_exchange_credentials(exchange_ref)
            account_index = str(credentials.get("account_index") or "").strip()
            if not account_index or account_index in seen_accounts:
                continue
            seen_accounts.add(account_index)
            try:
                payload = await self._request_json(
                    "GET",
                    f"{self.lighter_api_base}/api/v1/account",
                    params={"by": "index", "value": account_index},
                )
                account = (payload.get("accounts") or [])[0]
                for position in account.get("positions") or []:
                    raw_total = position.get("total_funding_paid_out")
                    symbol = str(position.get("symbol") or "").strip().upper()
                    # Lighter retains cumulative funding for previously traded
                    # markets even after a position is flat.
                    if not symbol or raw_total is None:
                        continue
                    values[f"{exchange_ref}|{symbol}"] = _decimal(raw_total)
            except Exception as exc:
                errors.append(f"{exchange_ref}: {type(exc).__name__}")

        if errors:
            self.record_lighter_failure(effective_now)
            raise RuntimeError("Lighter funding sample failed: " + ", ".join(errors))
        if seen_accounts:
            self.record_lighter_sample(values, now=effective_now)

    def record_lighter_sample(self, values: dict[str, Decimal], *, now: datetime) -> None:
        effective_now = _as_msk(now)
        state = self._read_state()
        previous = {key: _decimal(value) for key, value in state.get("lighter_values", {}).items()}
        normalized = {key: _decimal(value) for key, value in values.items()}
        last_sample_raw = state.get("lighter_last_sample_at")
        last_sample = datetime.fromisoformat(last_sample_raw).astimezone(MSK) if last_sample_raw else None
        outage_raw = state.pop("lighter_outage_started_at", None)
        outage_started = datetime.fromisoformat(outage_raw).astimezone(MSK) if outage_raw else None

        if not state.get("lighter_coverage_started_at"):
            state["lighter_coverage_started_at"] = effective_now.isoformat()

        if last_sample is not None:
            gap_too_large = (effective_now - last_sample).total_seconds() > self.max_lighter_sample_gap_sec
            if gap_too_large or outage_started is not None:
                self._mark_lighter_incomplete_between(state, outage_started or last_sample, effective_now)
            if set(previous) != set(normalized):
                self._mark_lighter_incomplete_between(state, last_sample, effective_now)

        window_key = self._window_end_for_observation(effective_now).isoformat()
        symbols = sorted({_symbol_from_sample_key(key) for key in normalized})
        known_symbols = set((state.setdefault("lighter_symbols_by_window", {})).get(window_key, []))
        state["lighter_symbols_by_window"][window_key] = sorted(known_symbols | set(symbols))

        window_accruals = state.setdefault("lighter_accruals", {}).setdefault(window_key, {})
        for key in sorted(set(previous) & set(normalized)):
            delta = normalized[key] - previous[key]
            if delta == 0:
                continue
            symbol = _symbol_from_sample_key(key)
            # Lighter's cumulative field is explicitly a payout: a positive
            # change is funding received and a negative change is funding paid.
            window_accruals[symbol] = str(_decimal(window_accruals.get(symbol)) + delta)

        state["lighter_values"] = {key: str(value) for key, value in normalized.items()}
        state["lighter_last_sample_at"] = effective_now.isoformat()
        self._prune_state(state, effective_now)
        self._write_state(state)

    def record_lighter_failure(self, now: datetime) -> None:
        effective_now = _as_msk(now)
        state = self._read_state()
        if not state.get("lighter_outage_started_at"):
            state["lighter_outage_started_at"] = state.get("lighter_last_sample_at", effective_now.isoformat())
        self._write_state(state)

    def lighter_rows_for_window(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[FundingRow], str | None]:
        if not self._enabled_refs("lighter"):
            return [], None
        state = self._read_state()
        coverage_raw = state.get("lighter_coverage_started_at")
        if not coverage_raw or datetime.fromisoformat(coverage_raw).astimezone(MSK) > _as_msk(start):
            return [], "Lighter — полный период 24 часа пока не накоплен."

        last_sample_raw = state.get("lighter_last_sample_at")
        window_key = _as_msk(end).isoformat()
        incomplete_windows = set(state.get("lighter_incomplete_windows", []))
        if (
            not last_sample_raw
            or datetime.fromisoformat(last_sample_raw).astimezone(MSK) < _as_msk(end)
            or window_key in incomplete_windows
            or state.get("lighter_outage_started_at")
        ):
            return [], "Lighter — неполные данные за период; отчёт не будет отправлен автоматически."

        accruals = state.get("lighter_accruals", {}).get(window_key, {})
        symbols = set(state.get("lighter_symbols_by_window", {}).get(window_key, [])) | set(accruals)
        rows = [FundingRow("Lighter", symbol, _decimal(accruals.get(symbol))) for symbol in sorted(symbols)]
        return rows, None

    async def build_report(self, now: datetime | None = None) -> FundingReport:
        effective_now = _as_msk(now or datetime.now(tz=MSK))
        start, end = self.completed_window(effective_now)
        rows, warnings = await self._hyperliquid_rows(start, end)
        variational_refs = self._enabled_refs("variational")
        if variational_refs:
            try:
                totals = await fetch_variational_funding(
                    self.credential_store, variational_refs, start, end,
                    self._request_json, self.variational_api_base,
                )
                rows.extend(FundingRow("Variational", symbol, amount) for symbol, amount in totals.items())
            except Exception as exc:
                warnings.append(f"Variational — не удалось получить полную историю: {type(exc).__name__}.")
        bingx_refs = self._enabled_refs("bingx")
        if bingx_refs:
            try:
                totals = await fetch_bingx_funding(
                    self.credential_store, bingx_refs, start, end,
                    self._request_json, self.bingx_api_base,
                )
                rows.extend(FundingRow("BingX", symbol, amount) for symbol, amount in totals.items())
            except Exception as exc:
                warnings.append(f"BingX — не удалось получить полную историю: {type(exc).__name__}.")
        okx_refs = self._enabled_refs("okx")
        if okx_refs:
            try:
                totals = await fetch_okx_funding(
                    self.credential_store, okx_refs, start, end,
                    self._request_json, self.okx_api_base, now=effective_now,
                )
                rows.extend(FundingRow("OKX", symbol, amount) for symbol, amount in totals.items())
            except Exception as exc:
                warnings.append(f"OKX — не удалось получить полную историю: {type(exc).__name__}.")
        lighter_rows, lighter_warning = self.lighter_rows_for_window(start, end)
        rows.extend(lighter_rows)
        if lighter_warning:
            warnings.append(lighter_warning)
        rows.sort(key=lambda row: (-row.amount_usd, row.exchange, row.symbol))
        return FundingReport(
            start=start,
            end=end,
            rows=rows,
            warnings=warnings,
            complete=not warnings,
        )

    async def _hyperliquid_rows(
        self,
        start: datetime,
        end: datetime,
    ) -> tuple[list[FundingRow], list[str]]:
        totals: dict[str, Decimal] = {}
        warnings: list[str] = []
        seen_addresses: set[str] = set()
        start_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)

        for exchange_ref in self._enabled_refs("hyperliquid"):
            credentials = self.credential_store.get_exchange_credentials(exchange_ref)
            address = str(credentials.get("user_address") or "").strip().lower()
            if not address:
                warnings.append(f"Hyperliquid ({exchange_ref}) — отсутствует user_address.")
                continue
            if address in seen_addresses:
                continue
            seen_addresses.add(address)
            try:
                account_totals: dict[str, Decimal] = {}
                seen_records: set[tuple[Any, ...]] = set()
                cursor_ms = start_ms
                while cursor_ms < end_ms:
                    payload = await self._request_json(
                        "POST",
                        f"{self.hyperliquid_api_base}/info",
                        json={
                            "type": "userFunding",
                            "user": address,
                            "startTime": cursor_ms,
                            "endTime": end_ms,
                        },
                    )
                    if not isinstance(payload, list):
                        raise ValueError("unexpected userFunding payload")
                    page_max_time = cursor_ms
                    for item in payload:
                        item_time = int(item.get("time") or 0)
                        if not item_time:
                            raise ValueError("userFunding record has no timestamp")
                        page_max_time = max(page_max_time, item_time)
                        if not (start_ms <= item_time < end_ms):
                            continue
                        delta = item.get("delta") or {}
                        record_key = (
                            item_time,
                            delta.get("coin"),
                            delta.get("usdc"),
                            delta.get("szi"),
                            delta.get("fundingRate"),
                        )
                        if record_key in seen_records:
                            continue
                        seen_records.add(record_key)
                        symbol = str(delta.get("coin") or "").split(":")[-1].strip().upper()
                        if symbol:
                            account_totals[symbol] = account_totals.get(symbol, Decimal("0")) + _decimal(delta.get("usdc"))
                    if len(payload) < 500:
                        break
                    if page_max_time <= cursor_ms:
                        raise ValueError("userFunding pagination made no progress")
                    cursor_ms = page_max_time
                for symbol, value in account_totals.items():
                    totals[symbol] = totals.get(symbol, Decimal("0")) + value
            except Exception as exc:
                warnings.append(f"Hyperliquid ({exchange_ref}) — не удалось получить историю: {type(exc).__name__}.")

        if warnings:
            return [], warnings
        return [FundingRow("Hyperliquid", symbol, value) for symbol, value in sorted(totals.items())], []

    def should_send(self, chat_id: str, now: datetime) -> bool:
        local_now = _as_msk(now)
        today_boundary = datetime.combine(local_now.date(), _BOUNDARY_TIME, tzinfo=MSK)
        if local_now < today_boundary:
            return False
        return not self.was_sent(chat_id, today_boundary)

    def was_sent(self, chat_id: str, window_end: datetime) -> bool:
        key = f"{str(chat_id)}|{_as_msk(window_end).isoformat()}"
        sent = self._read_state().get("sent_windows", {})
        return key in sent

    def mark_sent(self, chat_id: str, window_end: datetime) -> None:
        state = self._read_state()
        key = f"{str(chat_id)}|{_as_msk(window_end).isoformat()}"
        raw_sent = state.get("sent_windows", {})
        if isinstance(raw_sent, list):
            sent = {item: "1970-01-01T00:00:00+00:00" for item in raw_sent}
        else:
            sent = dict(raw_sent)
        sent[key] = datetime.now(tz=MSK).isoformat()
        state["sent_windows"] = sent
        self._write_state(state)

    @staticmethod
    def _mark_lighter_incomplete_between(state: dict[str, Any], start: datetime, end: datetime) -> None:
        local_start = _as_msk(start)
        local_end = _as_msk(end)
        end_probe = local_end - timedelta(microseconds=1) if local_end > local_start else local_end
        first_window_end = _report_window_end_for_moment(local_start)
        last_window_end = _report_window_end_for_moment(end_probe)
        incomplete = set(state.get("lighter_incomplete_windows", []))
        cursor = first_window_end
        while cursor <= last_window_end:
            incomplete.add(cursor.isoformat())
            cursor += timedelta(days=1)
        state["lighter_incomplete_windows"] = sorted(incomplete)

    def _enabled_refs(self, base_exchange: str) -> list[str]:
        return [
            ref
            for ref in self.credential_store.list_enabled_exchanges()
            if self.credential_store.get_base_exchange(ref) == base_exchange
        ]

    async def _request_json(self, method: str, url: str, **kwargs: Any) -> Any:
        if self._request_json_override is not None:
            return await self._request_json_override(method, url, **kwargs)
        async with httpx.AsyncClient(timeout=self.request_timeout_sec) as client:
            response = await client.request(method, url, **kwargs)
            response.raise_for_status()
            return response.json()

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {}
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_state(self, state: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_path = tempfile.mkstemp(prefix=f".{self.state_path.name}.", dir=self.state_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(temporary_path, self.state_path)
        finally:
            if os.path.exists(temporary_path):
                os.unlink(temporary_path)

    @staticmethod
    def _prune_state(state: dict[str, Any], now: datetime) -> None:
        cutoff = _as_msk(now) - timedelta(days=45)
        for field in ("lighter_accruals", "lighter_symbols_by_window"):
            values = state.get(field, {})
            state[field] = {
                key: value
                for key, value in values.items()
                if datetime.fromisoformat(key).astimezone(MSK) >= cutoff
            }
        state["lighter_incomplete_windows"] = [
            key
            for key in state.get("lighter_incomplete_windows", [])
            if datetime.fromisoformat(key).astimezone(MSK) >= cutoff
        ]


def render_funding_report(report: FundingReport) -> str:
    lines = [
        "Фандинг за день",
        f"Период: {report.start.astimezone(MSK):%d.%m.%Y %H:%M} — {report.end.astimezone(MSK):%d.%m.%Y %H:%M} МСК",
        "",
    ]
    if report.rows:
        lines.extend(
            f"{row.exchange} — {row.symbol} — {_format_usd(row.amount_usd)}"
            for row in report.rows
        )
        lines.extend(["", f"Итого — {_format_usd(report.total_usd)}"])
    else:
        lines.append("Начислений за период не найдено.")
    if report.warnings:
        lines.extend(["", *[f"⚠️ {warning}" for warning in report.warnings]])
    return "\n".join(lines)


def _format_usd(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{rounded:+.2f} USD"


def _decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"invalid decimal value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"non-finite decimal value: {value!r}")
    return result


def _report_window_end_for_moment(value: datetime) -> datetime:
    local_value = _as_msk(value)
    boundary = datetime.combine(local_value.date(), _BOUNDARY_TIME, tzinfo=MSK)
    return boundary if local_value < boundary else boundary + timedelta(days=1)


def _symbol_from_sample_key(key: str) -> str:
    return key.rsplit("|", 1)[-1].upper()


def _as_msk(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(MSK)
