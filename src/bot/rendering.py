from __future__ import annotations


def _fmt_usd(value: float) -> str:
    return f"{value:,.2f} USD"


def render_portfolio_text(status: dict) -> str:
    risk = status["risk"]
    down = [c["exchange"] for c in status.get("connector_statuses", []) if not c.get("ok")]
    connector_line = "Connectors: all ok" if not down else f"Connectors down: {', '.join(down)}"

    top_accounts = sorted(status.get("accounts", []), key=lambda item: item.get("equity_usd", 0), reverse=True)
    top_lines = [f"- {item['exchange'].title()}: {_fmt_usd(item['equity_usd'])}" for item in top_accounts[:5]]

    return "\n".join(
        [
            "Portfolio summary",
            f"Equity: {_fmt_usd(status['total_equity_usd'])}",
            f"Available margin: {_fmt_usd(status['total_available_margin_usd'])}",
            f"Maintenance margin: {_fmt_usd(status['total_maintenance_margin_usd'])}",
            f"Net delta: {_fmt_usd(risk['net_delta_usd'])}",
            f"Risk: {risk['risk_level']}",
            connector_line,
            "Top balances:",
            *top_lines,
        ]
    )


def render_daily_report_text(current: dict, previous: dict | None) -> str:
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
