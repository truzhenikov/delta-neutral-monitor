from __future__ import annotations

import asyncio
import contextlib

from aiogram import Bot, Dispatcher
from aiogram.filters import Command
from aiogram.types import Message

from src.config import get_settings
from src.deps import get_alerting_service, get_monitoring_service, get_status_service


def render_status_text(status: dict) -> str:
    risk = status["risk"]
    down = [c["exchange"] for c in status.get("connector_statuses", []) if not c.get("ok")]
    connector_line = "Connectors: all ok" if not down else f"Connectors down: {', '.join(down)}"
    return (
        "Portfolio status\n"
        f"Equity: {status['total_equity_usd']:.2f} USD\n"
        f"Available margin: {status['total_available_margin_usd']:.2f} USD\n"
        f"Maintenance margin: {status['total_maintenance_margin_usd']:.2f} USD\n"
        f"Risk: {risk['risk_level']}\n"
        f"Net delta: {risk['net_delta_usd']:.2f} USD\n"
        f"Margin ratio: {risk['margin_ratio']:.3f}\n"
        f"{connector_line}\n"
    )


def render_risk_text(status: dict) -> str:
    risk = status["risk"]
    warnings = risk["warnings"]
    lines = [
        f"Risk level: {risk['risk_level']}",
        f"Net delta: {risk['net_delta_usd']:.2f} USD",
        f"Margin ratio: {risk['margin_ratio']:.3f}",
        f"Min liq distance: {risk['min_liq_distance_pct']}",
    ]
    if warnings:
        lines.append("Warnings:")
        lines.extend([f"- {w}" for w in warnings])
    else:
        lines.append("Warnings: none")
    return "\n".join(lines)


def render_positions_text(status: dict) -> str:
    lines = ["Open positions:"]
    for account in status["accounts"]:
        lines.append(f"[{account['exchange']}]")
        for p in account["positions"]:
            lines.append(
                f"{p['symbol']} {p['side']} size={p['size']} mark={p['mark_price']} liq={p['liquidation_price']}"
            )
    return "\n".join(lines)


async def alert_loop(bot: Bot, chat_id: str, stop_event: asyncio.Event) -> None:
    settings = get_settings()
    monitoring = get_monitoring_service()
    status_service = get_status_service()
    alerting_service = get_alerting_service()

    while not stop_event.is_set():
        accounts, connector_statuses = await monitoring.collect_with_status()
        snapshot = status_service.build_status(accounts, connector_statuses=connector_statuses)
        messages = alerting_service.collect_alert_messages(snapshot)
        for text in messages:
            await bot.send_message(chat_id=chat_id, text=text)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.alert_poll_interval_sec)
        except TimeoutError:
            continue


async def run_bot() -> None:
    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty")

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()

    @dp.message(Command("start"))
    async def start_cmd(message: Message) -> None:
        await message.answer("Delta Neutral Monitor bot is running. Commands: /status /risk /positions")

    @dp.message(Command("status"))
    async def status_cmd(message: Message) -> None:
        monitoring = get_monitoring_service()
        status_service = get_status_service()
        accounts, connector_statuses = await monitoring.collect_with_status()
        snapshot = status_service.build_status(accounts, connector_statuses=connector_statuses).model_dump()
        await message.answer(render_status_text(snapshot))

    @dp.message(Command("risk"))
    async def risk_cmd(message: Message) -> None:
        monitoring = get_monitoring_service()
        status_service = get_status_service()
        accounts, connector_statuses = await monitoring.collect_with_status()
        snapshot = status_service.build_status(accounts, connector_statuses=connector_statuses).model_dump()
        await message.answer(render_risk_text(snapshot))

    @dp.message(Command("positions"))
    async def positions_cmd(message: Message) -> None:
        monitoring = get_monitoring_service()
        status_service = get_status_service()
        accounts, connector_statuses = await monitoring.collect_with_status()
        snapshot = status_service.build_status(accounts, connector_statuses=connector_statuses).model_dump()
        await message.answer(render_positions_text(snapshot))

    stop_event = asyncio.Event()
    task = None
    if settings.telegram_alert_chat_id.strip():
        task = asyncio.create_task(alert_loop(bot, settings.telegram_alert_chat_id.strip(), stop_event))

    try:
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    asyncio.run(run_bot())
