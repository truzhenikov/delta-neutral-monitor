from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class TelegramPreferencesService:
    def __init__(self, state_path: Path, admin_chat_ids: list[str], daily_report_hour_utc: int = 7) -> None:
        self.state_path = state_path
        self.admin_chat_ids = set(admin_chat_ids)
        self.daily_report_hour_utc = daily_report_hour_utc

    def get_chat(self, chat_id: str) -> dict[str, Any]:
        state = self._read_state()
        chat = state.setdefault("chats", {}).get(chat_id)
        if chat is None:
            chat = self._default_chat_settings()
        else:
            chat = {**self._default_chat_settings(), **chat}
        return chat

    def set_alerts_enabled(self, chat_id: str, enabled: bool) -> dict[str, Any]:
        return self._update_chat(chat_id, {"alerts_enabled": bool(enabled)})

    def set_daily_report_enabled(self, chat_id: str, enabled: bool) -> dict[str, Any]:
        return self._update_chat(chat_id, {"daily_report_enabled": bool(enabled)})

    def mark_daily_report_sent(self, chat_id: str, date_iso: str) -> None:
        self._update_chat(chat_id, {"last_daily_report_date": date_iso})

    def list_daily_report_chat_ids(self) -> list[str]:
        state = self._read_state()
        chats = state.get("chats", {})
        return sorted([chat_id for chat_id, payload in chats.items() if payload.get("daily_report_enabled")])

    def list_alert_chat_ids(self) -> list[str]:
        state = self._read_state()
        chats = state.get("chats", {})
        return sorted([chat_id for chat_id, payload in chats.items() if payload.get("alerts_enabled")])

    def is_admin(self, chat_id: str) -> bool:
        return chat_id in self.admin_chat_ids

    def _update_chat(self, chat_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        state = self._read_state()
        chats = state.setdefault("chats", {})
        chat = {**self._default_chat_settings(), **chats.get(chat_id, {}), **changes}
        chats[chat_id] = chat
        self._write_state(state)
        return chat

    def _default_chat_settings(self) -> dict[str, Any]:
        return {
            "alerts_enabled": False,
            "daily_report_enabled": False,
            "daily_report_hour_utc": self.daily_report_hour_utc,
            "authorized": False,
            "last_daily_report_date": None,
        }

    def _read_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"chats": {}}
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def _write_state(self, payload: dict[str, Any]) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_path.replace(self.state_path)
