from __future__ import annotations

from html import escape

COPY_BLOCK_TOTAL_LABEL = "Total"

def _fmt_usd(value: float) -> str:
    return f"{value:,.2f} USD"


def _real_leverage(account: dict) -> float:
    equity = float(account.get("equity_usd") or 0.0)
    if equity == 0:
        return 0.0
    return float(account.get("total_notional_usd") or 0.0) / equity


def _portfolio_real_leverage(status: dict) -> float:
    equity = float(status.get("total_equity_usd") or 0.0)
    if equity == 0:
        return 0.0
    total_notional = sum(float(item.get("total_notional_usd") or 0.0) for item in status.get("accounts", []))
    return total_notional / equity


def _fmt_copy_block_decimal(value: float) -> str:
    return f"{value:.2f}"


def _copy_block_label(exchange: str) -> str:
    return exchange.replace("_", " ").title()


def render_copy_block(status: dict | None) -> str:
    if not status:
        return ""

    accounts = sorted(
        status.get("accounts", []),
        key=lambda item: float(item.get("equity_usd") or 0.0),
        reverse=True,
    )
    if not accounts:
        return ""

    headers = [_copy_block_label(str(item.get("exchange", ""))) for item in accounts]
    values = [_fmt_copy_block_decimal(float(item.get("equity_usd") or 0.0)) for item in accounts]
    total_value = _fmt_copy_block_decimal(sum(float(item.get("equity_usd") or 0.0) for item in accounts))
    headers.append(COPY_BLOCK_TOTAL_LABEL)
    values.append(total_value)

    widths = [max(len(header), len(value)) for header, value in zip(headers, values)]
    header_row = "  ".join(header.ljust(width) for header, width in zip(headers, widths))
    value_row = "  ".join(value.ljust(width) for value, width in zip(values, widths))
    block_text = f"{header_row}\n{value_row}"
    return f"<pre>{escape(block_text)}</pre>"


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
            f"Real leverage: {_portfolio_real_leverage(status):.2f}x",
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
    copy_block = render_copy_block(status)
    if copy_block:
        lines.extend([
            "",
            copy_block,
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
