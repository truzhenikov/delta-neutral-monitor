from __future__ import annotations

import asyncio
import contextlib
import httpx
import logging
import time
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aiogram import Bot

from src.bot.command_logic import (
    build_alert_settings_reply,
    build_daily_reply,
    build_daily_snapshots_reply,
    build_portfolio_reply,
    set_alert_min_liq_distance,
    toggle_alerts,
    toggle_daily_reports,
)
from src.bot.keyboards import (
    build_alert_settings_keyboard,
    build_exchange_toggle_keyboard,
    build_main_menu_keyboard,
    build_remove_exchange_keyboard,
    build_setup_exchange_keyboard,
    parse_alert_settings_callback,
    parse_exchange_toggle_callback,
    parse_main_menu_button,
    parse_remove_exchange_callback,
    parse_setup_exchange_callback,
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


def parse_percentage_value(text: str) -> float | None:
    normalized = text.strip().replace(",", ".").replace("%", "")
    if not normalized:
        return None
    try:
        value = float(normalized)
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


async def send_alerts_for_snapshot(
    bot: Any,
    snapshot,
    preferences,
    alerting_service,
    bootstrap_chat_id: str = "",
) -> None:
    chat_ids = resolve_alert_chat_ids(preferences, bootstrap_chat_id=bootstrap_chat_id)
    if not chat_ids:
        return
    delivered_keys: set[str] = set()
    for chat_id in chat_ids:
        chat_settings = preferences.get_chat(chat_id)
        liq_threshold = float(chat_settings.get("alert_min_liq_distance_pct", 12.0))
        pending_alerts = alerting_service.collect_pending_alerts_for_liq_threshold(
            snapshot,
            min_liq_distance_pct=liq_threshold,
        )
        for alert in pending_alerts:
            sent = await safe_send_message(bot, chat_id, alert.text, context="alert")
            if sent:
                delivered_keys.add(alert.key)
    for key in delivered_keys:
        alerting_service.mark_sent(key)


async def send_due_daily_reports(bot: Any, preferences, daily_report_service, status: dict | None = None, now: datetime | None = None) -> None:
    effective_now = now or datetime.now(timezone.utc)
    report_day_key = daily_report_service.report_day_key(effective_now)
    if status is not None:
        daily_report_service.capture_snapshot(status, now=effective_now)
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


async def send_heartbeat_if_due(
    bot: Any,
    preferences,
    *,
    bootstrap_chat_id: str = "",
    heartbeat_interval_sec: int = 3600,
    last_heartbeat_at: float | None,
    now_monotonic: float | None = None,
) -> float | None:
    if heartbeat_interval_sec <= 0:
        return last_heartbeat_at
    current_monotonic = time.monotonic() if now_monotonic is None else now_monotonic
    if last_heartbeat_at is None:
        return current_monotonic
    if current_monotonic - last_heartbeat_at < heartbeat_interval_sec:
        return last_heartbeat_at

    chat_ids = resolve_alert_chat_ids(preferences, bootstrap_chat_id=bootstrap_chat_id)
    delivered = False
    heartbeat_text = f"HEARTBEAT: bot is running ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')})"
    for chat_id in chat_ids:
        sent = await safe_send_message(bot, chat_id, heartbeat_text, context="heartbeat")
        delivered = delivered or sent
    return current_monotonic if delivered else last_heartbeat_at


async def alert_loop(bot: Bot, stop_event: asyncio.Event) -> None:
    settings = get_settings()
    alerting_service = get_alerting_service()
    preferences = get_telegram_preferences_service()
    last_heartbeat_at: float | None = None

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
            last_heartbeat_at = await send_heartbeat_if_due(
                bot,
                preferences,
                bootstrap_chat_id=settings.telegram_alert_chat_id,
                heartbeat_interval_sec=getattr(settings, "heartbeat_interval_sec", 3600),
                last_heartbeat_at=last_heartbeat_at,
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
    from aiogram import Bot, Dispatcher, F
    from aiogram.filters import Command
    from aiogram.types import CallbackQuery, Message

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
    awaiting_alert_liq_distance_chat_ids: set[str] = set()

    async def show_main_menu(message: Message) -> None:
        preferences = get_telegram_preferences_service()
        include_admin = preferences.is_admin(str(message.chat.id))
        await message.answer(
            "Бот запущен. Можно использовать команды или кнопки меню ниже.",
            reply_markup=build_main_menu_keyboard(include_admin=include_admin),
        )

    async def send_status_reply(message: Message) -> None:
        snapshot = await collect_status_snapshot()
        await message.answer(render_status_text(snapshot))

    async def send_portfolio_reply(message: Message) -> None:
        snapshot = await collect_status_snapshot()
        await message.answer(build_portfolio_reply(snapshot))

    async def send_risk_reply(message: Message) -> None:
        snapshot = await collect_status_snapshot()
        await message.answer(render_risk_text(snapshot))

    async def send_positions_reply(message: Message) -> None:
        snapshot = await collect_status_snapshot()
        await message.answer(render_positions_text(snapshot))

    async def send_daily_reply(message: Message) -> None:
        daily_report_service = get_daily_report_service()
        report = daily_report_service.build_latest_report()
        status = await collect_status_snapshot()
        if report is None:
            await message.answer(
                build_daily_reply({"daily_changes": []}, status),
                parse_mode="HTML",
            )
            return
        current, previous = report
        await message.answer(build_daily_reply({"daily_changes": [current, previous]}, status), parse_mode="HTML")

    async def send_daily_snapshots_reply(message: Message) -> None:
        daily_report_service = get_daily_report_service()
        rows = [current for current, _previous in daily_report_service.build_recent_reports(limit=10)]
        await message.answer(build_daily_snapshots_reply({"daily_changes": rows}))

    async def send_alert_settings_reply(message: Message) -> None:
        preferences = get_telegram_preferences_service()
        awaiting_alert_liq_distance_chat_ids.discard(str(message.chat.id))
        await message.answer(
            build_alert_settings_reply(preferences, str(message.chat.id)),
            reply_markup=build_alert_settings_keyboard(),
        )

    async def set_alerts_enabled(message: Message, enabled: bool) -> None:
        preferences = get_telegram_preferences_service()
        await message.answer(
            toggle_alerts(preferences, str(message.chat.id), enabled),
            reply_markup=build_alert_settings_keyboard(),
        )

    async def set_daily_enabled(message: Message, enabled: bool) -> None:
        preferences = get_telegram_preferences_service()
        await message.answer(
            toggle_daily_reports(preferences, str(message.chat.id), enabled),
            reply_markup=build_alert_settings_keyboard(),
        )

    async def prompt_alert_liq_distance(message: Message) -> None:
        awaiting_alert_liq_distance_chat_ids.add(str(message.chat.id))
        await message.answer(
            "Введите порог дистанции до ликвидации в процентах.\n"
            "Пример: 8 или 8.5\n"
            "Напишите /cancel для отмены."
        )

    async def apply_alert_liq_distance(message: Message) -> bool:
        chat_id = str(message.chat.id)
        if chat_id not in awaiting_alert_liq_distance_chat_ids:
            return False
        value = parse_percentage_value(message.text or "")
        if value is None:
            await message.answer("Не понял значение. Введите положительное число в процентах, например 8 или 8.5.")
            return True
        preferences = get_telegram_preferences_service()
        awaiting_alert_liq_distance_chat_ids.discard(chat_id)
        await message.answer(
            set_alert_min_liq_distance(preferences, chat_id, value),
            reply_markup=build_alert_settings_keyboard(),
        )
        return True

    async def run_setup_flow(message: Message, command_text: str) -> None:
        reply = setup_flow.start_setup(str(message.chat.id), command_text)
        if len(command_text.strip().split(maxsplit=1)) == 1 and reply.startswith("Выберите биржу"):
            await message.answer(reply, reply_markup=build_setup_exchange_keyboard())
            return
        await message.answer(reply)

    async def run_enable_exchange(message: Message, command_text: str) -> None:
        reply = setup_flow.enable_exchange(str(message.chat.id), command_text)
        if len(command_text.strip().split(maxsplit=1)) == 1 and reply.startswith("Укажите биржу"):
            await message.answer(reply, reply_markup=build_exchange_toggle_keyboard("enable_exchange", get_credential_store()))
            return
        await message.answer(reply)

    async def run_disable_exchange(message: Message, command_text: str) -> None:
        reply = setup_flow.disable_exchange(str(message.chat.id), command_text)
        if len(command_text.strip().split(maxsplit=1)) == 1 and reply.startswith("Укажите биржу"):
            await message.answer(reply, reply_markup=build_exchange_toggle_keyboard("disable_exchange", get_credential_store()))
            return
        await message.answer(reply)

    async def run_list_exchanges(message: Message) -> None:
        await message.answer(setup_flow.list_exchanges(str(message.chat.id)))

    async def run_remove_exchange(message: Message, command_text: str) -> None:
        reply = setup_flow.remove_exchange(str(message.chat.id), command_text)
        if len(command_text.strip().split(maxsplit=1)) == 1 and reply.startswith("Выберите профиль"):
            await message.answer(reply, reply_markup=build_remove_exchange_keyboard(get_credential_store()))
            return
        await message.answer(reply)

    async def run_cancel(message: Message) -> None:
        chat_id = str(message.chat.id)
        awaiting_alert_liq_distance_chat_ids.discard(chat_id)
        await message.answer(setup_flow.cancel(chat_id))

    @dp.message(Command("start"))
    async def start_cmd(message: Message) -> None:
        await show_main_menu(message)

    @dp.message(Command("menu"))
    async def menu_cmd(message: Message) -> None:
        await show_main_menu(message)

    @dp.message(Command("status"))
    async def status_cmd(message: Message) -> None:
        await send_status_reply(message)

    @dp.message(Command("portfolio"))
    async def portfolio_cmd(message: Message) -> None:
        await send_portfolio_reply(message)

    @dp.message(Command("risk"))
    async def risk_cmd(message: Message) -> None:
        await send_risk_reply(message)

    @dp.message(Command("positions"))
    async def positions_cmd(message: Message) -> None:
        await send_positions_reply(message)

    @dp.message(Command("daily"))
    async def daily_cmd(message: Message) -> None:
        await send_daily_reply(message)

    @dp.message(Command("daily_snapshots"))
    async def daily_snapshots_cmd(message: Message) -> None:
        await send_daily_snapshots_reply(message)

    @dp.message(Command("alerts"))
    async def alerts_cmd(message: Message) -> None:
        await send_alert_settings_reply(message)

    @dp.message(Command("alerts_on"))
    async def alerts_on_cmd(message: Message) -> None:
        await set_alerts_enabled(message, True)

    @dp.message(Command("alerts_off"))
    async def alerts_off_cmd(message: Message) -> None:
        await set_alerts_enabled(message, False)

    @dp.message(Command("daily_on"))
    async def daily_on_cmd(message: Message) -> None:
        await set_daily_enabled(message, True)

    @dp.message(Command("daily_off"))
    async def daily_off_cmd(message: Message) -> None:
        await set_daily_enabled(message, False)

    @dp.message(Command("setup"))
    async def setup_cmd(message: Message) -> None:
        await run_setup_flow(message, message.text or "/setup")

    @dp.callback_query(F.data.startswith("setup_exchange:"))
    async def setup_exchange_callback(callback: CallbackQuery) -> None:
        exchange = parse_setup_exchange_callback(callback.data)
        if exchange is None:
            await callback.answer("Некорректный выбор биржи.")
            return
        reply = setup_flow.select_exchange(str(callback.message.chat.id), exchange)
        if callback.message is not None:
            await callback.message.answer(reply)
        await callback.answer()

    @dp.message(Command("enable_exchange"))
    async def enable_exchange_cmd(message: Message) -> None:
        await run_enable_exchange(message, message.text or "/enable_exchange")

    @dp.callback_query(F.data.startswith("enable_exchange:"))
    async def enable_exchange_callback(callback: CallbackQuery) -> None:
        exchange = parse_exchange_toggle_callback("enable_exchange", callback.data)
        if exchange is None:
            await callback.answer("Некорректный выбор профиля.")
            return
        if exchange == "cancel":
            if callback.message is not None:
                await callback.message.answer("Включение профиля отменено.")
            await callback.answer()
            return
        reply = setup_flow.enable_exchange(str(callback.message.chat.id), f"/enable_exchange {exchange}")
        if callback.message is not None:
            await callback.message.answer(reply)
        await callback.answer()

    @dp.message(Command("disable_exchange"))
    async def disable_exchange_cmd(message: Message) -> None:
        await run_disable_exchange(message, message.text or "/disable_exchange")

    @dp.callback_query(F.data.startswith("disable_exchange:"))
    async def disable_exchange_callback(callback: CallbackQuery) -> None:
        exchange = parse_exchange_toggle_callback("disable_exchange", callback.data)
        if exchange is None:
            await callback.answer("Некорректный выбор профиля.")
            return
        if exchange == "cancel":
            if callback.message is not None:
                await callback.message.answer("Выключение профиля отменено.")
            await callback.answer()
            return
        reply = setup_flow.disable_exchange(str(callback.message.chat.id), f"/disable_exchange {exchange}")
        if callback.message is not None:
            await callback.message.answer(reply)
        await callback.answer()

    @dp.message(Command("exchanges"))
    async def exchanges_cmd(message: Message) -> None:
        await run_list_exchanges(message)

    @dp.message(Command("remove_exchange"))
    async def remove_exchange_cmd(message: Message) -> None:
        await run_remove_exchange(message, message.text or "/remove_exchange")

    @dp.callback_query(F.data.startswith("remove_exchange:"))
    async def remove_exchange_callback(callback: CallbackQuery) -> None:
        exchange = parse_remove_exchange_callback(callback.data)
        if exchange is None:
            await callback.answer("Некорректный выбор профиля.")
            return
        if exchange == "cancel":
            if callback.message is not None:
                await callback.message.answer("Удаление профиля отменено.")
            await callback.answer()
            return
        reply = setup_flow.remove_exchange(str(callback.message.chat.id), f"/remove_exchange {exchange}")
        if callback.message is not None:
            await callback.message.answer(reply)
        await callback.answer()

    @dp.callback_query(F.data.startswith("alert_settings:"))
    async def alert_settings_callback(callback: CallbackQuery) -> None:
        action = parse_alert_settings_callback(callback.data)
        if callback.message is None or action is None:
            await callback.answer("Некорректное действие.")
            return
        if action == "set_liq_distance":
            awaiting_alert_liq_distance_chat_ids.add(str(callback.message.chat.id))
            await callback.message.answer(
                "Введите порог дистанции до ликвидации в процентах.\n"
                "Пример: 8 или 8.5\n"
                "Напишите /cancel для отмены."
            )
            await callback.answer()
            return
        if action == "cancel":
            awaiting_alert_liq_distance_chat_ids.discard(str(callback.message.chat.id))
            await callback.message.answer("Настройка алертов отменена.")
            await callback.answer()
            return
        await callback.answer("Неизвестное действие.")

    @dp.message(Command("cancel"))
    async def cancel_cmd(message: Message) -> None:
        await run_cancel(message)

    @dp.message()
    async def setup_flow_message_handler(message: Message) -> None:
        if await apply_alert_liq_distance(message):
            return
        if setup_flow.has_active_session(str(message.chat.id)):
            await message.answer(await setup_flow.handle_message_async(str(message.chat.id), message.text or ""))
            return
        menu_command = parse_main_menu_button(message.text or "")
        if menu_command == "/status":
            await send_status_reply(message)
        elif menu_command == "/portfolio":
            await send_portfolio_reply(message)
        elif menu_command == "/risk":
            await send_risk_reply(message)
        elif menu_command == "/positions":
            await send_positions_reply(message)
        elif menu_command == "/daily":
            await send_daily_reply(message)
        elif menu_command == "/daily_snapshots":
            await send_daily_snapshots_reply(message)
        elif menu_command == "/alerts":
            await send_alert_settings_reply(message)
        elif menu_command == "/alerts_on":
            await set_alerts_enabled(message, True)
        elif menu_command == "/alerts_off":
            await set_alerts_enabled(message, False)
        elif menu_command == "/daily_on":
            await set_daily_enabled(message, True)
        elif menu_command == "/daily_off":
            await set_daily_enabled(message, False)
        elif menu_command == "/setup":
            await run_setup_flow(message, "/setup")
        elif menu_command == "/enable_exchange":
            await run_enable_exchange(message, "/enable_exchange")
        elif menu_command == "/disable_exchange":
            await run_disable_exchange(message, "/disable_exchange")
        elif menu_command == "/exchanges":
            await run_list_exchanges(message)
        elif menu_command == "/remove_exchange":
            await run_remove_exchange(message, "/remove_exchange")
        elif menu_command == "/cancel":
            await run_cancel(message)

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
