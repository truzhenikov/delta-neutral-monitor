from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable


Validator = Callable[[dict[str, str]], Awaitable[str]]


@dataclass(frozen=True)
class CredentialValidationResult:
    ok: bool
    message: str
    checked: bool = True


class CredentialValidationService:
    def __init__(self, validators: dict[str, Validator] | None = None) -> None:
        self.validators = {key.strip().lower(): value for key, value in (validators or {}).items()}

    async def validate(self, exchange: str, payload: dict[str, str]) -> CredentialValidationResult:
        normalized_exchange = exchange.strip().lower()
        validator = self.validators.get(normalized_exchange)
        if validator is None:
            return CredentialValidationResult(ok=False, message=f"Unsupported exchange for validation: {exchange}")
        try:
            result = await validator(dict(payload))
        except Exception as exc:  # pragma: no cover - defensive boundary
            return CredentialValidationResult(ok=False, message=f"Validation failed: {exc}")
        if isinstance(result, CredentialValidationResult):
            return result
        return CredentialValidationResult(ok=True, message=str(result))


def build_default_credential_validation_service(supported_exchanges: list[str]) -> CredentialValidationService:
    async def _placeholder(_: dict[str, str]) -> CredentialValidationResult:
        return CredentialValidationResult(
            ok=True,
            checked=False,
            message="Проверка учетных данных не выполнялась; данные сохранены.",
        )

    return CredentialValidationService(validators={exchange: _placeholder for exchange in supported_exchanges})