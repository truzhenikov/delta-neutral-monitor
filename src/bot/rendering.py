from __future__ import annotations

EXCEL_EXCHANGE_COLUMNS: list[tuple[str, str]] = [
    ("hyperliquid", "HL"),
    ("aden", "Aden"),
    ("kucoin", "Kucoin"),
    ("extended", "Extended"),
    ("okx", "OKX"),
    ("bitget", "Bitget"),
    ("bingx", "BingX"),
]


def _fmt_usd(value: float) -> str:
    return f"{value:,.2f} USD"


def _real_leverage(account: dict) -> float:
    equity = float(account.get("equity_usd") or 0.0)
    if equity == 0:
        return 0.0
    return float(account.get("total_notional_usd") or 0.0) / equity


def _fmt_excel_decimal(value: float) -> str:
    return f"{value:.2f}".replace(".", ",")


def render_excel_exchange_header_row() -> str:
    return "\t".join(label for _exchange, label in EXCEL_EXCHANGE_COLUMNS)


def render_excel_exchange_row(status: dict | None) -> str:
    if not status:
        return ""
    accounts = {str(item.get("exchange", "")).lower(): float(item.get("equity_usd") or 0.0) for item in status.get("accounts", [])}
    ordered_values = [_fmt_excel_decimal(accounts.get(exchange, 0.0)) for exchange, _label in EXCEL_EXCHANGE_COLUMNS]
    return "\t".join(ordered_values)


def render_portfolio_text(status: dict) -> str:
    risk = status["risk"]
    down = [c["exchange"] for c in status.get("connector_statuses", []) if not c.get("ok")]
    connector_line = "Connectors: all ok" if not down else f"Connectors down: {', '.join(down)}"

    accounts = sorted(status.get("accounts", []), key=lambda item: item.get("equity_usd", 0), reverse=True)
    balance_lines = [
        f"- {item['exchange'].title()}: {_fmt_usd(item['equity_usd'])} (real lev {_real_leverage(item):.2f}x)"
        for item in accounts
    ]

    return "\n".join(
        [
            "Portfolio summary",
            f"Equity: {_fmt_usd(status['total_equity_usd'])}",
            f"Available margin: {_fmt_usd(status['total_available_margin_usd'])}",
            f"Maintenance margin: {_fmt_usd(status['total_maintenance_margin_usd'])}",
            f"Net delta: {_fmt_usd(risk['net_delta_usd'])}",
            f"Risk: {risk['risk_level']}",
            connector_line,
            "Balances by exchange:",
            *balance_lines,
        ]
    )


def render_daily_report_text(current: dict, previous: dict | None, status: dict | None = None) -> str:
    lines = [
        "Daily portfolio report",
        f"Date: {current['date']}",
        f"Today equity: {_fmt_usd(current['equity_usd'])}",
    ]
    if previous is None:
        lines.append("No previous day snapshot yet")
        return "\n".join(lines)

    change = current["equity_usd"] - previous["equity_usd"]
    pct = 0.0 if previous["equity_usd"] == 0 else (change / previous["equity_usd"]) * 100
    lines.extend(
        [
            f"Previous close ({previous['date']}): {_fmt_usd(previous['equity_usd'])}",
            f"Change: {change:+,.2f} USD ({pct:+.2f}%)",
        ]
    )
    excel_row = render_excel_exchange_row(status)
    if excel_row:
        lines.extend([
            "",
            render_excel_exchange_header_row(),
            excel_row,
        ])
    return "\n".join(lines)


def render_alert_settings_text(settings: dict) -> str:
    alerts_enabled = "ON" if settings.get("alerts_enabled") else "OFF"
    daily_enabled = "ON" if settings.get("daily_report_enabled") else "OFF"
    hour = int(settings.get("daily_report_hour_utc", 7))
    return "\n".join(
        [
            "Alert settings",
            f"Liquidation/risk alerts: {alerts_enabled}",
            f"Daily report: {daily_enabled}",
            f"Scheduled hour (UTC): {hour:02d}:00",
        ]
    )
