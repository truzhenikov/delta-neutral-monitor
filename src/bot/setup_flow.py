from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from src.services.credential_store import CredentialStore
from src.services.credential_validation import CredentialValidationService, build_default_credential_validation_service
from src.services.telegram_preferences import TelegramPreferencesService


FIELD_LABELS: dict[tuple[str, str], str] = {
    ("hyperliquid", "user_address"): "wallet address",
}


@dataclass
class SetupSession:
    exchange: str | None = None
    profile_name: str | None = None
    awaiting_profile_name: bool = False
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

    def start_setup(self, chat_id: str, command_text: str = "/setup") -> str:
        if not self.preferences.is_admin(chat_id):
            return "Доступ к настройке бирж разрешен только администраторам."
        session = SetupSession()
        parts = command_text.strip().split()
        if len(parts) >= 3:
            return self.select_exchange(chat_id, parts[1].strip(), " ".join(parts[2:]).strip())
        if len(parts) == 2 and parts[1].strip():
            return self._start_direct_setup(chat_id, parts[1].strip())
        self._sessions[chat_id] = session
        exchanges = ", ".join(sorted(CredentialStore.SUPPORTED_EXCHANGES))
        return f"Выберите биржу для настройки: {exchanges}"

    def select_exchange(self, chat_id: str, exchange: str, profile_name: str | None = None) -> str:
        if not self.preferences.is_admin(chat_id):
            return "Доступ к настройке бирж разрешен только администраторам."
        try:
            normalized_exchange = self.credential_store.get_base_exchange(exchange)
        except ValueError:
            exchanges = ", ".join(sorted(CredentialStore.SUPPORTED_EXCHANGES))
            return f"Неизвестная биржа. Доступные варианты: {exchanges}"
        session = self._sessions.get(chat_id, SetupSession())
        session.exchange = normalized_exchange
        session.payload = {}
        if profile_name is None:
            session.profile_name = None
            session.awaiting_profile_name = True
            session.field_index = -1
            self._sessions[chat_id] = session
            return self._prompt_for_profile_name(session)
        try:
            session.profile_name = CredentialStore.normalize_profile_name(profile_name)
        except ValueError:
            session.profile_name = None
            session.awaiting_profile_name = True
            session.field_index = -1
            self._sessions[chat_id] = session
            return "Имя профиля не должно быть пустым и не может содержать двоеточие. Попробуйте ещё раз."
        session.awaiting_profile_name = False
        session.field_index = 0
        self._sessions[chat_id] = session
        return self._prompt_for_current_field(session)

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
            return self.select_exchange(chat_id, normalized_text)

        if session.awaiting_profile_name:
            try:
                session.profile_name = CredentialStore.normalize_profile_name(normalized_text)
            except ValueError:
                return "Имя профиля не должно быть пустым и не может содержать двоеточие. Попробуйте ещё раз."
            session.awaiting_profile_name = False
            session.field_index = 0
            return self._prompt_for_current_field(session)

        field_name = self._required_fields(session)[session.field_index]
        session.payload[field_name] = normalized_text
        session.field_index += 1
        if session.field_index < len(self._required_fields(session)):
            return self._prompt_for_current_field(session)

        exchange_ref = self._exchange_ref(session)
        self.credential_store.set_exchange_credentials(exchange_ref, session.payload)
        validation = await self.validation_service.validate(session.exchange, session.payload)
        self._sessions.pop(chat_id, None)
        if validation.checked:
            status_line = "Проверка пройдена." if validation.ok else "Проверка не пройдена."
        else:
            status_line = "Проверка учетных данных не выполнялась."
        return f"Данные для {exchange_ref} сохранены и биржа включена. {status_line} {validation.message}"

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
        statuses = self.credential_store.list_exchange_statuses()
        lines = ["Биржи:"]
        for item in statuses:
            state = "ON" if item["enabled"] else "OFF"
            config_state = "configured" if item["configured"] else "not configured"
            masked = ", ".join(f"{key}={value}" for key, value in item["credentials"].items()) or "no credentials"
            lines.append(f"- {item['exchange']}: {state}, {config_state}, {masked}")
        lines.append("Команды: /setup <exchange>, /setup <exchange> <profile>, /enable_exchange <exchange>, /disable_exchange <exchange>, /remove_exchange <exchange>")
        return "\n".join(lines)

    def enable_exchange(self, chat_id: str, command_text: str) -> str:
        return self._set_exchange_enabled(chat_id, command_text, enabled=True)

    def disable_exchange(self, chat_id: str, command_text: str) -> str:
        return self._set_exchange_enabled(chat_id, command_text, enabled=False)

    def remove_exchange(self, chat_id: str, command_text: str) -> str:
        if not self.preferences.is_admin(chat_id):
            return "Доступ к удалению бирж разрешен только администраторам."
        parts = command_text.strip().split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            configured = self.credential_store.list_configured_exchanges()
            if not configured:
                return "Сохраненных профилей для удаления пока нет."
            exchanges = ", ".join(item["exchange"] for item in configured)
            return f"Выберите профиль для удаления: {exchanges}"
        exchange = parts[1].strip()
        try:
            normalized = self.credential_store.normalize_exchange_ref(exchange)
        except ValueError:
            return f"Биржа {exchange} не поддерживается."
        self.credential_store.remove_exchange(normalized)
        return f"Биржа {normalized} удалена из конфигурации."

    def _set_exchange_enabled(self, chat_id: str, command_text: str, *, enabled: bool) -> str:
        if not self.preferences.is_admin(chat_id):
            return "Доступ к изменению списка бирж разрешен только администраторам."
        parts = command_text.strip().split(maxsplit=1)
        if len(parts) != 2 or not parts[1].strip():
            command = "/enable_exchange" if enabled else "/disable_exchange"
            return f"Укажите биржу: {command} <exchange>"
        exchange = parts[1].strip()
        try:
            normalized = self.credential_store.normalize_exchange_ref(exchange)
        except ValueError:
            return f"Биржа {exchange} не поддерживается."
        self.credential_store.set_exchange_enabled(normalized, enabled)
        if enabled:
            credentials = self.credential_store.get_exchange_credentials(normalized)
            if credentials:
                return f"Биржа {normalized} включена. Данные уже сохранены."
            return f"Биржа {normalized} включена, но API-данные ещё не добавлены. Используйте /setup {normalized}."
        return f"Биржа {normalized} выключена. Проверка и алерты по ней остановлены."

    def _start_direct_setup(self, chat_id: str, exchange: str) -> str:
        if not self.preferences.is_admin(chat_id):
            return "Доступ к настройке бирж разрешен только администраторам."
        try:
            normalized_exchange = self.credential_store.get_base_exchange(exchange)
        except ValueError:
            exchanges = ", ".join(sorted(CredentialStore.SUPPORTED_EXCHANGES))
            return f"Неизвестная биржа. Доступные варианты: {exchanges}"
        session = SetupSession(exchange=normalized_exchange, profile_name=None, awaiting_profile_name=False, field_index=0)
        self._sessions[chat_id] = session
        return self._prompt_for_current_field(session)

    def _required_fields(self, session: SetupSession) -> tuple[str, ...]:
        assert session.exchange is not None
        return CredentialStore.SUPPORTED_EXCHANGES[session.exchange]

    def _exchange_ref(self, session: SetupSession) -> str:
        assert session.exchange is not None
        return CredentialStore.format_exchange_ref(session.exchange, session.profile_name)

    def _prompt_for_profile_name(self, session: SetupSession) -> str:
        assert session.exchange is not None
        return f"Введите имя профиля для {session.exchange} (например, main). Для отмены используйте /cancel."

    def _prompt_for_current_field(self, session: SetupSession) -> str:
        field_name = self._required_fields(session)[session.field_index]
        display_name = FIELD_LABELS.get((session.exchange, field_name), field_name)
        target = self._exchange_ref(session) if session.profile_name else session.exchange
        return f"Введите значение поля {display_name} для {target}. Для отмены используйте /cancel."
