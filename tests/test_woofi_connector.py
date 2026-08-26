import asyncio
import base64

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.config import get_settings
from src.connectors.factory import build_connectors
from src.connectors.woofi_connector import WoofiRealConnector
from src.services.credential_store import CredentialStore


def _base58_encode(raw: bytes) -> str:
    alphabet = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, rem = divmod(n, 58)
        out = alphabet[rem] + out
    return alphabet[0] * (len(raw) - len(raw.lstrip(b"\0"))) + (out or alphabet[0])


class StubWoofiConnector(WoofiRealConnector):
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    async def _get(self, base_url, path, params=None, headers=None):
        self.calls.append((base_url, path, headers))
        return self.payloads[path]


def test_woofi_is_supported_by_factories_and_credentials() -> None:
    assert build_connectors(["woofi"], use_mock_data=False)[0].exchange == "woofi"
    assert build_connectors(["woofi"], use_mock_data=True)[0].exchange == "woofi"
    assert CredentialStore.SUPPORTED_EXCHANGES["woofi"] == ("account_id", "orderly_key", "orderly_secret")


def test_woofi_auth_and_snapshot_mapping(monkeypatch) -> None:
    private = Ed25519PrivateKey.generate()
    secret = _base58_encode(private.private_bytes_raw())
    public = base64.b64encode(private.public_key().public_bytes_raw()).decode()
    get_settings.cache_clear()
    monkeypatch.setenv("WOOFI_ACCOUNT_ID", "demo-account")
    monkeypatch.setenv("WOOFI_ORDERLY_KEY", public)
    monkeypatch.setenv("WOOFI_ORDERLY_SECRET", secret)
    monkeypatch.setenv("WOOFI_API_BASE", "https://api.orderly.org")

    connector = StubWoofiConnector({
        "/v1/account_info": {"success": True, "data": {"account_value": 1000, "free_collateral": 800, "maintenance_margin_ratio": 0.05}},
        "/v1/positions": {"success": True, "data": [{"symbol": "PERP_BTC_USDC", "position_qty": "-0.2", "average_open_price": "50000", "mark_price": "51000", "est_liq_price": "70000"}]},
    })
    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.equity_usd == 1000
    assert snapshot.available_margin_usd == 800
    assert snapshot.maintenance_margin_usd == 50
    assert snapshot.positions[0].side == "short"
    assert snapshot.positions[0].size == 0.2
    assert [item[1] for item in connector.calls] == ["/v1/account_info", "/v1/positions"]
    assert connector.calls[0][2]["orderly-key"] == f"ed25519:{public}"
    assert connector.calls[0][2]["orderly-signature"]
    get_settings.cache_clear()
