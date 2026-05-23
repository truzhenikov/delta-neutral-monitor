from __future__ import annotations

import asyncio
import contextlib
import httpx
import logging
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiogram import Bot

from src.bot.command_logic import (
    build_alert_settings_reply,
    build_daily_reply,
    build_portfolio_reply,
    toggle_alerts,
    toggle_daily_reports,
)
from src.bot.setup_flow import TelegramSetupFlow
from src.config import get_settings
from src.core.schemas import StatusOut
from src.deps import (
    get_alerting_service,
    get_credential_store,
    get_credential_validation_service,
    get_daily_report_service,
    get_history_service,
    get_telegram_preferences_service,
)

logger = logging.getLogger(__name__)


async def collect_status_snapshot() -> dict:
    settings = get_settings()
    api_base = f"http://127.0.0.1:{settings.api_port}"
    async with httpx.AsyncClient(timeout=settings.request_timeout_sec) as client:
        response = await client.get(f"{api_base}/v1/status", headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()


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


def resolve_alert_chat_ids(preferences, bootstrap_chat_id: str = "") -> list[str]:
    chat_ids = set(preferences.list_alert_chat_ids())
    if bootstrap_chat_id.strip():
        chat_ids.add(bootstrap_chat_id.strip())
    return sorted(chat_ids)


async def safe_send_message(bot: Any, chat_id: str, text: str, *, context: str, parse_mode: str | None = None) -> bool:
    try:
        kwargs = {"chat_id": chat_id, "text": text}
        if parse_mode is not None:
            kwargs["parse_mode"] = parse_mode
        await bot.send_message(**kwargs)
    except Exception as exc:
        logger.warning("telegram_send_failed context=%s chat_id=%s error=%s", context, chat_id, exc)
        return False
    return True


async def send_alerts_for_snapshot(
    bot: Any,
    snapshot,
    preferences,
    alerting_service,
    bootstrap_chat_id: str = "",
) -> None:
    pending_alerts = alerting_service.collect_pending_alerts(snapshot)
    if not pending_alerts:
        return
    chat_ids = resolve_alert_chat_ids(preferences, bootstrap_chat_id=bootstrap_chat_id)
    for alert in pending_alerts:
        delivered = False
        for chat_id in chat_ids:
            sent = await safe_send_message(bot, chat_id, alert.text, context="alert")
            delivered = delivered or sent
        if delivered:
            alerting_service.mark_sent(alert.key)


async def send_due_daily_reports(bot: Any, preferences, daily_report_service, status: dict | None = None, now: datetime | None = None) -> None:
    effective_now = now or datetime.now(timezone.utc)
    report_day_key = daily_report_service.report_day_key(effective_now)
    report = daily_report_service.build_report_for_date(date.fromisoformat(report_day_key))
    if report is None:
        return
    current, previous = report
    text = build_daily_reply({"daily_changes": [current, previous]}, status)
    for chat_id in preferences.list_daily_report_chat_ids():
        chat_settings = preferences.get_chat(chat_id)
        if not daily_report_service.should_send(chat_settings, effective_now):
            continue
        sent = await safe_send_message(bot, chat_id, text, context="daily_report", parse_mode="HTML")
        if sent:
            preferences.mark_daily_report_sent(chat_id, report_day_key)


async def alert_loop(bot: Bot, stop_event: asyncio.Event) -> None:
    settings = get_settings()
    alerting_service = get_alerting_service()
    preferences = get_telegram_preferences_service()

    while not stop_event.is_set():
        try:
            snapshot = StatusOut.model_validate(await collect_status_snapshot())
            await send_alerts_for_snapshot(
                bot,
                snapshot,
                preferences,
                alerting_service,
                bootstrap_chat_id=settings.telegram_alert_chat_id,
            )
        except Exception as exc:
            logger.warning("alert_loop_iteration_failed error=%s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.alert_poll_interval_sec)
        except TimeoutError:
            continue


async def daily_report_loop(bot: Bot, stop_event: asyncio.Event) -> None:
    settings = get_settings()
    preferences = get_telegram_preferences_service()
    daily_report_service = get_daily_report_service()

    while not stop_event.is_set():
        try:
            status = await collect_status_snapshot()
            await send_due_daily_reports(bot, preferences, daily_report_service, status=status)
        except Exception as exc:
            logger.warning("daily_report_loop_iteration_failed error=%s", exc)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.alert_poll_interval_sec)
        except TimeoutError:
            continue


async def run_bot() -> None:
    from aiogram import Bot, Dispatcher
    from aiogram.filters import Command
    from aiogram.types import Message

    settings = get_settings()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is empty")

    bot = Bot(token=settings.telegram_bot_token)
    dp = Dispatcher()
    setup_flow = TelegramSetupFlow(
        preferences=get_telegram_preferences_service(),
        credential_store=get_credential_store(),
        validation_service=get_credential_validation_service(),
    )

    @dp.message(Command("start"))
    async def start_cmd(message: Message) -> None:
        await message.answer(
            "Delta Neutral Monitor bot is running. Commands: /status /portfolio /risk /positions /daily /alerts /alerts_on /alerts_off /daily_on /daily_off /setup /exchanges /remove_exchange /cancel"
        )

    @dp.message(Command("status"))
    async def status_cmd(message: Message) -> None:
        snapshot = await collect_status_snapshot()
        await message.answer(render_status_text(snapshot))

    @dp.message(Command("portfolio"))
    async def portfolio_cmd(message: Message) -> None:
        snapshot = await collect_status_snapshot()
        await message.answer(build_portfolio_reply(snapshot))

    @dp.message(Command("risk"))
    async def risk_cmd(message: Message) -> None:
        snapshot = await collect_status_snapshot()
        await message.answer(render_risk_text(snapshot))

    @dp.message(Command("positions"))
    async def positions_cmd(message: Message) -> None:
        snapshot = await collect_status_snapshot()
        await message.answer(render_positions_text(snapshot))

    @dp.message(Command("daily"))
    async def daily_cmd(message: Message) -> None:
        history_service = get_history_service()
        history = history_service.build_history_response().model_dump(mode="json")
        status = await collect_status_snapshot()
        await message.answer(build_daily_reply(history, status), parse_mode="HTML")

    @dp.message(Command("alerts"))
    async def alerts_cmd(message: Message) -> None:
        preferences = get_telegram_preferences_service()
        await message.answer(build_alert_settings_reply(preferences, str(message.chat.id)))

    @dp.message(Command("alerts_on"))
    async def alerts_on_cmd(message: Message) -> None:
        preferences = get_telegram_preferences_service()
        await message.answer(toggle_alerts(preferences, str(message.chat.id), True))

    @dp.message(Command("alerts_off"))
    async def alerts_off_cmd(message: Message) -> None:
        preferences = get_telegram_preferences_service()
        await message.answer(toggle_alerts(preferences, str(message.chat.id), False))

    @dp.message(Command("daily_on"))
    async def daily_on_cmd(message: Message) -> None:
        preferences = get_telegram_preferences_service()
        await message.answer(toggle_daily_reports(preferences, str(message.chat.id), True))

    @dp.message(Command("daily_off"))
    async def daily_off_cmd(message: Message) -> None:
        preferences = get_telegram_preferences_service()
        await message.answer(toggle_daily_reports(preferences, str(message.chat.id), False))

    @dp.message(Command("setup"))
    async def setup_cmd(message: Message) -> None:
        await message.answer(setup_flow.start_setup(str(message.chat.id)))

    @dp.message(Command("exchanges"))
    async def exchanges_cmd(message: Message) -> None:
        await message.answer(setup_flow.list_exchanges(str(message.chat.id)))

    @dp.message(Command("remove_exchange"))
    async def remove_exchange_cmd(message: Message) -> None:
        await message.answer(setup_flow.remove_exchange(str(message.chat.id), message.text or ""))

    @dp.message(Command("cancel"))
    async def cancel_cmd(message: Message) -> None:
        await message.answer(setup_flow.cancel(str(message.chat.id)))

    @dp.message()
    async def setup_flow_message_handler(message: Message) -> None:
        if not setup_flow.has_active_session(str(message.chat.id)):
            return
        await message.answer(await setup_flow.handle_message_async(str(message.chat.id), message.text or ""))

    stop_event = asyncio.Event()
    tasks = [
        asyncio.create_task(alert_loop(bot, stop_event)),
        asyncio.create_task(daily_report_loop(bot, stop_event)),
    ]

    try:
        await dp.start_polling(bot)
    finally:
        stop_event.set()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task


if __name__ == "__main__":
    asyncio.run(run_bot())
