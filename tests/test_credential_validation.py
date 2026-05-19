from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_validation_returns_success_message() -> None:
    from src.services.credential_validation import CredentialValidationService

    async def validator(payload: dict[str, str]) -> str:
        assert payload == {"api_key": "abc"}
        return "ok"

    service = CredentialValidationService(validators={"aden": validator})

    result = await service.validate("aden", {"api_key": "abc"})

    assert result.ok is True
    assert result.message == "ok"


@pytest.mark.asyncio
async def test_validation_returns_concise_error_on_auth_failure() -> None:
    from src.services.credential_validation import CredentialValidationService

    async def validator(payload: dict[str, str]) -> str:
        raise RuntimeError("auth failed")

    service = CredentialValidationService(validators={"aden": validator})

    result = await service.validate("aden", {"api_key": "abc"})

    assert result.ok is False
    assert "auth failed" in result.message.lower()


@pytest.mark.asyncio
async def test_validation_rejects_unsupported_exchange() -> None:
    from src.services.credential_validation import CredentialValidationService

    service = CredentialValidationService(validators={})

    result = await service.validate("binance", {"api_key": "abc"})

    assert result.ok is False
    assert "unsupported" in result.message.lower()
