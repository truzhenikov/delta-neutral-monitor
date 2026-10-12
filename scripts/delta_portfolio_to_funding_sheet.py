#!/usr/bin/env python3
"""Write the delta-monitor Portfolio snapshot to today's monthly funding sheet row.

Normal mode is silent on success and fail-closed on stale/degraded/unknown data.
Use --dry-run to inspect the planned write without changing Google Sheets.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from zoneinfo import ZoneInfo

from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = "1uYuZ_O4AHf0ecFxZmlvn3SrbGuPY1KfYM8GW86GByGY"
SERVICE_ACCOUNT_FILE = "/root/.config/hermes/google/service-account-drive.json"
STATUS_URL = "http://127.0.0.1:8080/v1/status"
MOSCOW = ZoneInfo("Europe/Moscow")
MONTH_NAMES = {
    1: "январь", 2: "февраль", 3: "март", 4: "апрель",
    5: "май", 6: "июнь", 7: "июль", 8: "август",
    9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
}

# Exact refs prevent a newly-added profile from being silently written to a wrong column.
EXCHANGE_TO_HEADER = {
    "aden:aden-44c3": "Aden",
    "arcus:arcus-44c3": "Arcus",
    "aster:aster-3c3b": "Aster",
    "bingx:bingx-1": "BingX",
    "bitget:bitget-1": "Bitget",
    "extended:extended-44c3": "Extended",
    "hyperliquid:hl-3c3b": "HL",
    "hyperliquid:hl-e48b": "HL-48b",
    "variational:var-3c3b": "Var-3c3b",
    "gate": "Gate",
    "lighter:lighter-3c3b": "Lighter",
    "lighter-rh:robinhood": "Lighter-RH-3c3b",
    "lighter-rh:lighter-rh-44c3": "Lighter-RH-44c3",
    "mexc:mexc": "Mexc",
    "nado:main": "Nado",
    "nado:nado-44c3": "Nado-44c3",
    "ondo:ondo-3c3b": "Ondo",
    "ondo:ondo-44c3": "Ondo-44c3",
    "phemex:phemex": "Phemex",
    "risex:risex-f504": "RiseX",
    "txflow:txflow-44c3": "Txflow",
    "variational:main": "Var",
    "variational:var-e48b": "Var-48b",
    "woofi": "WooFi",
}

# The sheet has no OKX column; this is an intentional dust-only omission.
IGNORED_EXCHANGES = {"okx:okx-1"}
GOOGLE_EPOCH = date(1899, 12, 30)
MAX_SNAPSHOT_AGE_SECONDS = 600


def _fetch_status() -> dict:
    request = urllib.request.Request(STATUS_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        payload = json.load(response)
    if not isinstance(payload, dict):
        raise RuntimeError("delta-monitor returned a non-object status payload")
    return payload


def _parse_generated_at(value: str) -> datetime:
    if not value:
        raise RuntimeError("delta-monitor status has no generated_at timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _round_for_sheet(value: object) -> float:
    return float(Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _column_name(index_1_based: int) -> str:
    name = ""
    n = index_1_based
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return name


def _sheet_service():
    creds = service_account.Credentials.from_service_account_file(
        SERVICE_ACCOUNT_FILE,
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _target_sheet(today: date) -> str:
    return f"{MONTH_NAMES[today.month]} {today.year % 100:02d}"


def _expected_date_serial(today: date) -> int:
    return (today - GOOGLE_EPOCH).days


def build_plan(*, now: datetime | None = None) -> tuple[dict, list[str]]:
    now = now or datetime.now(MOSCOW)
    today = now.date()
    status = _fetch_status()
    blockers: list[str] = []

    generated_at = _parse_generated_at(str((status.get("risk") or {}).get("generated_at") or ""))
    age = (datetime.now(timezone.utc) - generated_at).total_seconds()
    if age < -60 or age > MAX_SNAPSHOT_AGE_SECONDS:
        blockers.append(f"status snapshot age is {age:.0f}s")

    connector_by_exchange = {
        str(item.get("exchange")): item
        for item in status.get("connector_statuses", [])
        if isinstance(item, dict)
    }
    accounts = [item for item in status.get("accounts", []) if isinstance(item, dict)]
    mapped: dict[str, float] = {}
    ignored: dict[str, float] = {}
    warnings: list[str] = []

    for account in accounts:
        exchange = str(account.get("exchange") or "")
        equity = _round_for_sheet(account.get("equity_usd"))
        connector = connector_by_exchange.get(exchange)
        if exchange in EXCHANGE_TO_HEADER:
            if not connector or not connector.get("ok"):
                error = (connector or {}).get("error") or "missing connector status"
                warnings.append(f"{exchange} is degraded; Portfolio fallback value was used: {error}")
            header = EXCHANGE_TO_HEADER[exchange]
            if header in mapped:
                blockers.append(f"duplicate destination header: {header}")
            mapped[header] = equity
        elif exchange in IGNORED_EXCHANGES:
            ignored[exchange] = equity
        elif abs(equity) > 0.004:
            blockers.append(f"unmapped account {exchange} has equity {equity:.2f}")

    if not mapped:
        blockers.append("no mapped portfolio accounts")

    service = _sheet_service()
    title = _target_sheet(today)
    meta = service.spreadsheets().get(
        spreadsheetId=SPREADSHEET_ID,
        fields="sheets(properties(title,sheetId))",
    ).execute()
    if not any(s["properties"]["title"] == title for s in meta.get("sheets", [])):
        blockers.append(f"target sheet does not exist: {title}")

    header_values = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{title}'!A2:AL2",
        valueRenderOption="FORMATTED_VALUE",
    ).execute().get("values", [[]])
    headers = header_values[0] if header_values else []
    header_positions: dict[str, list[str]] = {}
    for i, value in enumerate(headers):
        header = str(value).strip()
        if header:
            header_positions.setdefault(header, []).append(_column_name(i + 1))
    header_columns = {header: columns[0] for header, columns in header_positions.items()}
    for header in mapped:
        columns = header_positions.get(header, [])
        if not columns:
            blockers.append(f"destination header is missing: {header}")
        elif len(columns) > 1:
            blockers.append(
                f"duplicate destination header: {header} ({', '.join(columns)})"
            )

    date_values = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{title}'!A3:A50",
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute().get("values", [])
    expected_serial = _expected_date_serial(today)
    matching_rows = [i + 3 for i, row in enumerate(date_values) if row and row[0] == expected_serial]
    if len(matching_rows) != 1:
        blockers.append(f"expected exactly one row for {today.isoformat()}, found {len(matching_rows)}")
        row_number = None
    else:
        row_number = matching_rows[0]

    cells = {
        f"{header_columns[header]}{row_number}": value
        for header, value in mapped.items()
        if header in header_columns and row_number is not None
    }
    return {
        "date": today.isoformat(),
        "sheet": title,
        "row": row_number,
        "cells": cells,
        "ignored": ignored,
        "source_total_equity_usd": _round_for_sheet(status.get("total_equity_usd")),
        "mapped_total_equity_usd": _round_for_sheet(sum(mapped.values())),
        "generated_at": generated_at.isoformat(),
        "warnings": warnings,
    }, blockers


def apply_plan(plan: dict) -> None:
    service = _sheet_service()
    title = plan["sheet"]
    cells: dict[str, float] = plan["cells"]
    if not cells:
        raise RuntimeError("refusing to write an empty cell set")

    service.spreadsheets().values().batchUpdate(
        spreadsheetId=SPREADSHEET_ID,
        body={
            "valueInputOption": "RAW",
            "data": [
                {"range": f"'{title}'!{cell}", "values": [[value]]}
                for cell, value in sorted(cells.items())
            ],
        },
    ).execute()

    readback = service.spreadsheets().values().batchGet(
        spreadsheetId=SPREADSHEET_ID,
        ranges=[f"'{title}'!{cell}" for cell in sorted(cells)],
        valueRenderOption="UNFORMATTED_VALUE",
    ).execute().get("valueRanges", [])
    if len(readback) != len(cells):
        raise RuntimeError("Google Sheets read-back returned an incomplete result")
    for cell, result in zip(sorted(cells), readback, strict=True):
        rows = result.get("values", [])
        actual = rows[0][0] if rows and rows[0] else None
        if actual is None or abs(float(actual) - float(cells[cell])) > 1e-9:
            raise RuntimeError(f"read-back mismatch in {cell}: expected {cells[cell]}, got {actual}")

    row = int(plan["row"])
    header_values = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{title}'!A2:AL2",
        valueRenderOption="FORMATTED_VALUE",
    ).execute().get("values", [[]])
    headers = header_values[0] if header_values else []
    formula_headers = ["sum", "dif", "APY, day", "APY, mon", "накопительно, мес"]
    try:
        formula_indexes = [headers.index(name) + 1 for name in formula_headers]
    except ValueError as exc:
        raise RuntimeError(f"formula verification headers are missing: {formula_headers}") from exc
    expected_indexes = list(range(formula_indexes[0], formula_indexes[0] + len(formula_headers)))
    if formula_indexes != expected_indexes:
        raise RuntimeError(f"formula verification columns are not contiguous: {formula_indexes}")
    start_col = _column_name(formula_indexes[0])
    end_col = _column_name(formula_indexes[-1])
    calculated_rows = service.spreadsheets().values().get(
        spreadsheetId=SPREADSHEET_ID,
        range=f"'{title}'!{start_col}{row}:{end_col}{row}",
        valueRenderOption="FORMATTED_VALUE",
    ).execute().get("values", [[]])
    calculated = calculated_rows[0] if calculated_rows else []
    if len(calculated) < len(formula_headers) or any(str(value).startswith("#") for value in calculated):
        raise RuntimeError(
            f"formula verification failed in {start_col}{row}:{end_col}{row}: {calculated}"
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    plan, blockers = build_plan()
    if args.dry_run:
        print(json.dumps({"plan": plan, "blockers": blockers}, ensure_ascii=False, indent=2))
        return 0
    if blockers:
        raise RuntimeError("delta portfolio sheet update blocked: " + "; ".join(blockers))
    apply_plan(plan)
    if plan.get("warnings"):
        print(
            "⚠️ Дельта-портфель записан в "
            f"{plan['sheet']} за {plan['date']}, но использовано резервное значение: "
            + "; ".join(plan["warnings"])
        )
    elif args.verbose:
        print(json.dumps({"status": "updated_and_verified", **plan}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"DELTA PORTFOLIO SHEET ERROR: {exc}", file=sys.stderr)
        raise
