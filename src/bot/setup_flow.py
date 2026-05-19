from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.services.credential_store import CredentialStore
from src.services.credential_validation import CredentialValidationService, build_default_credential_validation_service
from src.services.telegram_preferences import TelegramPreferencesService


@dataclass
class SetupSession:
    exchange: str | None = None
    field_index: int = -1
    payload: dict[str, str] = field(default_factory=dict)


class TelegramSetupFlow:
    def __init__(
        self,
        preferences: TelegramPreferencesService,
        credential_store: CredentialStore,
        validation_service: CredentialValidationService | None = None,
    ) -> None:
        self.preferences = preferences
        self.credential_store = credential_store
        self.validation_service = validation_service or build_default_credential_validation_service(
            supported_exchanges=sorted(CredentialStore.SUPPORTED_EXCHANGES)
        )
        self._sessions: dict[str, SetupSession] = {}

    def has_active_session(self, chat_id: str) -> bool:
        return chat_id in self._sessions

    def start_setup(self, chat_id: str) -> str:
        if not self.preferences.is_admin(chat_id):
            return "Доступ к настройке бирж разрешен только администраторам."
        self._sessions[chat_id] = SetupSession()
        exchanges = ", ".join(sorted(CredentialStore.SUPPORTED_EXCHANGES))
        return f"Выберите биржу для настройки: {exchanges}"

    def cancel(self, chat_id: str) -> str:
        if self._sessions.pop(chat_id, None) is None:
            return "Нет активной настройки для отмены."
        return "Настройка отменена."

    async def handle_message_async(self, chat_id: str, text: str) -> str:
        session = self._sessions.get(chat_id)
        if session is None:
            return "Нет активной настройки. Используйте /setup."

        normalized_text = text.strip()
        if not normalized_text:
            return "Пустое значение не принято, отправьте непустое значение поля."

        if session.exchange is None:
            try:
                exchange = self.credential_store._normalize_exchange(normalized_text)
            except ValueError:
                exchanges = ", ".join(sorted(CredentialStore.SUPPORTED_EXCHANGES))
                return f"Неизвестная биржа. Доступные варианты: {exchanges}"
            session.exchange = exchange
            session.field_index = 0
            return self._prompt_for_current_field(session)

        field_name = self._required_fields(session)[session.field_index]
        session.payload[field_name] = normalized_text
        session.field_index += 1
        if session.field_index < len(self._required_fields(session)):
            return self._prompt_for_current_field(session)

        self.credential_store.set_exchange_credentials(session.exchange, session.payload)
        validation = await self.validation_service.validate(session.exchange, session.payload)
        self._sessions.pop(chat_id, None)
        status_line = "Проверка пройдена." if validation.ok else "Проверка не пройдена."
        return f"Данные для {session.exchange} сохранены. {status_line} {validation.message}"

    def handle_message(self, chat_id: str, text: str) -> Any:
        import asyncio

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.handle_message_async(chat_id, text))
        return self.handle_message_async(chat_id, text)

    def list_exchanges(self, chat_id: str) -> str:
        if not self.preferences.is_admin(chat_id):
            return "Доступ к списку бирж разрешен только администраторам."
        configured = self.credential_store.list_configured_exchanges()
        if not configured:
            return "Сохраненных бирж пока нет."
        lines = ["Сохраненные биржи:"]
        for item in configured:
            masked = ", ".join(f"{key}={value}" for key, value in item["credentials"].items())
            lines.append(f"- {item['exchange']}: {masked}")
        return "\n".join(lines)

    def remove_exchange(self, chat_id: str, command_text: str) -> str:
        if not self.preferences.is_admin(chat_id):
            return "Доступ к удалению бирж разрешен только администраторам."
        parts = command_text.strip().split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            return "Укажите биржу: /remove_exchange <exchange>"
        exchange = parts[1].strip()
        try:
            normalized = self.credential_store._normalize_exchange(exchange)
        except ValueError:
            return f"Биржа {exchange} не поддерживается."
        self.credential_store.remove_exchange(normalized)
        return f"Биржа {normalized} удалена."

    def _required_fields(self, session: SetupSession) -> tuple[str, ...]:
        assert session.exchange is not None
        return CredentialStore.SUPPORTED_EXCHANGES[session.exchange]

    def _prompt_for_current_field(self, session: SetupSession) -> str:
        field_name = self._required_fields(session)[session.field_index]
        return f"Введите значение поля {field_name} для {session.exchange}. Для отмены используйте /cancel."