from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src.services.credential_store import CredentialStore

_SETUP_CALLBACK_PREFIX = "setup_exchange:"
_REMOVE_CALLBACK_PREFIX = "remove_exchange:"
_REMOVE_CANCEL = "cancel"


@dataclass
class _FallbackInlineKeyboardButton:
    text: str
    callback_data: str


@dataclass
class _FallbackInlineKeyboardMarkup:
    inline_keyboard: list[list[_FallbackInlineKeyboardButton]]


def _keyboard_types() -> tuple[type[Any], type[Any]]:
    try:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
    except ModuleNotFoundError:
        return _FallbackInlineKeyboardButton, _FallbackInlineKeyboardMarkup
    return InlineKeyboardButton, InlineKeyboardMarkup


def build_setup_exchange_keyboard() -> Any:
    InlineKeyboardButton, InlineKeyboardMarkup = _keyboard_types()
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


def build_remove_exchange_keyboard(store: CredentialStore) -> Any:
    InlineKeyboardButton, InlineKeyboardMarkup = _keyboard_types()
    configured = store.list_configured_exchanges()
    rows = [[InlineKeyboardButton(text=item["exchange"], callback_data=f"{_REMOVE_CALLBACK_PREFIX}{item['exchange']}")] for item in configured]
    rows.append([InlineKeyboardButton(text="Отмена", callback_data=f"{_REMOVE_CALLBACK_PREFIX}{_REMOVE_CANCEL}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_setup_exchange_callback(callback_data: str | None) -> str | None:
    if not callback_data or not callback_data.startswith(_SETUP_CALLBACK_PREFIX):
        return None
    exchange = callback_data.removeprefix(_SETUP_CALLBACK_PREFIX).strip()
    return exchange or None


def parse_remove_exchange_callback(callback_data: str | None) -> str | None:
    if not callback_data or not callback_data.startswith(_REMOVE_CALLBACK_PREFIX):
        return None
    exchange = callback_data.removeprefix(_REMOVE_CALLBACK_PREFIX).strip()
    return exchange or None
