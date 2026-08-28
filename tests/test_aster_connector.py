from __future__ import annotations

from urllib.parse import urlencode

import pytest
from eth_account import Account
from eth_account.messages import encode_typed_data

from src.connectors.aster_connector import AsterRealConnector


PRIVATE_KEY = "0x0123456789012345678901234567890123456789012345678901234567890123"
SIGNER = Account.from_key(PRIVATE_KEY).address
USER = "0xf8C41AD2BB7A486aDADD336837eC76f468F83c3b"


def test_v3_signed_params_use_user_signer_and_eip712() -> None:
    connector = AsterRealConnector()
    original = connector._credentials
    connector._credentials = lambda: (USER, SIGNER, PRIVATE_KEY)
    try:
        params = connector._signed_params()
    finally:
        connector._credentials = original

    assert params["user"] == USER
    assert params["signer"] == SIGNER
    assert "signature" in params
    payload = urlencode(dict(sorted((k, v) for k, v in params.items() if k != "signature")))
    typed = {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Message": [{"name": "msg", "type": "string"}],
        },
        "primaryType": "Message",
        "domain": {"name": "AsterSignTransaction", "version": "1", "chainId": 1666, "verifyingContract": "0x0000000000000000000000000000000000000000"},
        "message": {"msg": payload},
    }
    recovered = Account.recover_message(
        encode_typed_data(full_message=typed), signature=params["signature"]
    )
    assert recovered == SIGNER


@pytest.mark.asyncio
async def test_snapshot_uses_v3_read_only_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AsterRealConnector, "_credentials", lambda self: (USER, SIGNER, PRIVATE_KEY))
    calls: list[str] = []

    async def fake_get(_self, _base: str, path: str, **kwargs):
        calls.append(path)
        if path == "/fapi/v3/account":
            return {"totalMarginBalance": "100", "availableBalance": "90", "totalMaintMargin": "1"}
        return [{"symbol": "BTCUSDT", "positionAmt": "2", "entryPrice": "10", "markPrice": "11", "leverage": "5", "liquidationPrice": "2"}]

    monkeypatch.setattr(AsterRealConnector, "_get", fake_get)
    snapshot = await AsterRealConnector().fetch_account_snapshot()
    assert calls == ["/fapi/v3/account", "/fapi/v3/positionRisk"]
    assert snapshot.equity_usd == 100
    assert len(snapshot.positions) == 1
    assert snapshot.positions[0].side == "long"
