from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.services.credential_store import CredentialStore

_SETUP_CALLBACK_PREFIX = "setup_exchange:"
_REMOVE_CALLBACK_PREFIX = "remove_exchange:"
_TOGGLE_CALLBACK_PREFIXES = {
    "enable_exchange": "enable_exchange:",
    "disable_exchange": "disable_exchange:",
}
_REMOVE_CANCEL = "cancel"
_MAIN_MENU_ROWS = [
    [("Статус", "/status"), ("Портфель", "/portfolio")],
    [("Риск", "/risk"), ("Позиции", "/positions")],
    [("День", "/daily"), ("Алерты", "/alerts")],
    [("Алерты ВКЛ", "/alerts_on"), ("Алерты ВЫКЛ", "/alerts_off")],
    [("День ВКЛ", "/daily_on"), ("День ВЫКЛ", "/daily_off")],
]
_ADMIN_MENU_ROWS = [
    [("Настроить биржу", "/setup"), ("Биржи", "/exchanges")],
    [("Включить биржу", "/enable_exchange"), ("Выключить биржу", "/disable_exchange")],
    [("Удалить биржу", "/remove_exchange"), ("Отмена", "/cancel")],
]
_MAIN_MENU_COMMANDS = {text: command for row in (_MAIN_MENU_ROWS + _ADMIN_MENU_ROWS) for text, command in row}


@dataclass
class _FallbackInlineKeyboardButton:
    text: str
    callback_data: str


@dataclass
class _FallbackInlineKeyboardMarkup:
    inline_keyboard: list[list[_FallbackInlineKeyboardButton]]


@dataclass
class _FallbackKeyboardButton:
    text: str


@dataclass
class _FallbackReplyKeyboardMarkup:
    keyboard: list[list[_FallbackKeyboardButton]]
    resize_keyboard: bool = True
    is_persistent: bool = True


def _inline_keyboard_types() -> tuple[type[Any], type[Any]]:
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    except ModuleNotFoundError:
        return _FallbackInlineKeyboardButton, _FallbackInlineKeyboardMarkup
    return InlineKeyboardButton, InlineKeyboardMarkup


def _reply_keyboard_types() -> tuple[type[Any], type[Any]]:
    try:
        from aiogram.types import KeyboardButton, ReplyKeyboardMarkup
    except ModuleNotFoundError:
        return _FallbackKeyboardButton, _FallbackReplyKeyboardMarkup
    return KeyboardButton, ReplyKeyboardMarkup


def build_main_menu_keyboard(*, include_admin: bool) -> Any:
    KeyboardButton, ReplyKeyboardMarkup = _reply_keyboard_types()
    menu_rows = list(_MAIN_MENU_ROWS)
    if include_admin:
        menu_rows.extend(_ADMIN_MENU_ROWS)
    keyboard = [[KeyboardButton(text=text) for text, _command in row] for row in menu_rows]
    return ReplyKeyboardMarkup(keyboard=keyboard, resize_keyboard=True, is_persistent=True)


def parse_main_menu_button(text: str | None) -> str | None:
    if text is None:
        return None
    return _MAIN_MENU_COMMANDS.get(text.strip())


def build_setup_exchange_keyboard() -> Any:
    InlineKeyboardButton, InlineKeyboardMarkup = _inline_keyboard_types()
    exchanges = sorted(CredentialStore.SUPPORTED_EXCHANGES)
    rows = []
    for index in range(0, len(exchanges), 2):
        row_exchanges = exchanges[index : index + 2]
        rows.append(
            [
                InlineKeyboardButton(text=exchange, callback_data=f"{_SETUP_CALLBACK_PREFIX}{exchange}")
                for exchange in row_exchanges
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_exchange_toggle_keyboard(action: str, store: CredentialStore) -> Any:
    prefix = _TOGGLE_CALLBACK_PREFIXES[action]
    return _build_exchange_selection_keyboard(store, prefix)


def build_remove_exchange_keyboard(store: CredentialStore) -> Any:
    return _build_exchange_selection_keyboard(store, _REMOVE_CALLBACK_PREFIX)


def _build_exchange_selection_keyboard(store: CredentialStore, callback_prefix: str) -> Any:
    InlineKeyboardButton, InlineKeyboardMarkup = _inline_keyboard_types()
    configured = store.list_configured_exchanges()
    rows = [[InlineKeyboardButton(text=item["exchange"], callback_data=f"{callback_prefix}{item['exchange']}")] for item in configured]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"{callback_prefix}{_REMOVE_CANCEL}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_setup_exchange_callback(callback_data: str | None) -> str | None:
    if not callback_data or not callback_data.startswith(_SETUP_CALLBACK_PREFIX):
        return None
    exchange = callback_data.removeprefix(_SETUP_CALLBACK_PREFIX).strip()
    return exchange or None


def parse_exchange_toggle_callback(action: str, callback_data: str | None) -> str | None:
    prefix = _TOGGLE_CALLBACK_PREFIXES[action]
    if not callback_data or not callback_data.startswith(prefix):
        return None
    exchange = callback_data.removeprefix(prefix).strip()
    return exchange or None


def parse_remove_exchange_callback(callback_data: str | None) -> str | None:
    if not callback_data or not callback_data.startswith(_REMOVE_CALLBACK_PREFIX):
        return None
    exchange = callback_data.removeprefix(_REMOVE_CALLBACK_PREFIX).strip()
    return exchange or None
