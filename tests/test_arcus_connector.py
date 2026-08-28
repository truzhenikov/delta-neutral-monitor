import asyncio

from src.config import get_settings
from src.connectors.arcus_connector import ArcusRealConnector
from src.connectors.factory import build_connectors
from src.services.credential_store import CredentialStore


class StubArcusConnector(ArcusRealConnector):
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    async def _get(self, base_url, path, params=None, headers=None):
        self.calls.append((base_url, path, params, headers))
        return self.payloads[path]


def test_arcus_is_supported_by_factories_and_credentials() -> None:
    assert build_connectors(["arcus"], use_mock_data=False)[0].exchange == "arcus"
    assert build_connectors(["arcus"], use_mock_data=True)[0].exchange == "arcus"
    assert CredentialStore.SUPPORTED_EXCHANGES["arcus"] == ("address", "account_index")


def test_arcus_maps_account_and_positions(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("ARCUS_ADDRESS", "0xAbC0000000000000000000000000000000000001")
    monkeypatch.setenv("ARCUS_ACCOUNT_INDEX", "2")
    connector = StubArcusConnector({
        "/v1/account": {
            "accountIndex": 2,
            "netQuoteBalance": "1000",
            "equity": "1012.5",
            "freeCollateral": "800.25",
        },
        "/v1/positions": {
            "positions": {
                "1": {
                    "marketDisplayName": "INTC-USD",
                    "side": "SHORT",
                    "size": "-40",
                    "averageEntryPrice": "89.49",
                    "markPx": "89.34",
                    "leverage": "10",
                    "marginMode": "CROSS",
                }
            }
        },
    })

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "arcus"
    assert snapshot.equity_usd == 1012.5
    assert snapshot.available_margin_usd == 800.25
    assert snapshot.positions[0].symbol == "INTC-USD"
    assert snapshot.positions[0].side == "short"
    assert snapshot.positions[0].size == 40
    assert snapshot.positions[0].entry_price == 89.49
    assert snapshot.positions[0].mark_price == 89.34
    assert snapshot.positions[0].liquidation_price is None
    assert connector.calls[0][1:] == ("/v1/account", {"address": "0xabc0000000000000000000000000000000000001", "accountIndex": 2}, None)
    assert connector.calls[1][1:] == ("/v1/positions", {"address": "0xabc0000000000000000000000000000000000001", "accountIndex": 2}, None)
    get_settings.cache_clear()
