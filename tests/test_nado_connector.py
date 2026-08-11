from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from src.connectors.factory import build_connectors
from src.connectors.real_connectors import (
    NadoRealConnector,
    RealConnectorNotConfiguredError,
    RealConnectorRequestError,
    _nado_subaccount_hex,
)
from src.services.credential_store import CredentialStore


X18 = 10**18


class StubNadoConnector(NadoRealConnector):
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]] = []
        self.account_payload: dict[str, Any] = {}
        self.isolated_payload: dict[str, Any] = {"status": "success", "data": {"isolated_positions": []}}
        self.symbols_payload: Any = []
        self.prices_payload: dict[str, Any] = {}

    async def _get(self, base_url: str, path: str, params=None, headers=None) -> Any:
        self.calls.append(("GET", f"{base_url}{path}", params, headers))
        if path == "/symbols":
            return self.symbols_payload
        if params and params.get("type") == "subaccount_info":
            return self.account_payload
        if params and params.get("type") == "isolated_positions":
            return self.isolated_payload
        raise AssertionError(f"Unexpected GET: {base_url}{path} {params}")

    async def _post(self, base_url: str, path: str, body: dict[str, Any], headers=None) -> Any:
        self.calls.append(("POST", f"{base_url}{path}", body, headers))
        return self.prices_payload


def _health(initial: int, maintenance: int, raw: int) -> list[dict[str, str]]:
    return [
        {"assets": "0", "liabilities": "0", "health": str(initial * X18)},
        {"assets": "0", "liabilities": "0", "health": str(maintenance * X18)},
        {"assets": "0", "liabilities": "0", "health": str(raw * X18)},
    ]


def _perp(product_id: int, amount_x18: int, v_quote_x18: int) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "balance": {
            "amount": str(amount_x18),
            "v_quote_balance": str(v_quote_x18),
            "last_cumulative_funding_x18": "0",
        },
    }


def test_nado_subaccount_hex_encodes_wallet_and_name() -> None:
    assert _nado_subaccount_hex(
        "0x7a5ec2748e9065794491a8d29dcf3f9edb8d7c43",
        "default",
    ) == "0x7a5ec2748e9065794491a8d29dcf3f9edb8d7c4364656661756c740000000000"


@pytest.mark.parametrize(
    ("wallet", "name"),
    [
        ("0x1234", "default"),
        ("not-hex", "default"),
        ("0x7a5ec2748e9065794491a8d29dcf3f9edb8d7c43", "name-is-too-long"),
    ],
)
def test_nado_subaccount_hex_rejects_invalid_identifiers(wallet: str, name: str) -> None:
    with pytest.raises(ValueError):
        _nado_subaccount_hex(wallet, name)


def test_build_connectors_includes_nado_real_and_mock() -> None:
    real = build_connectors(["nado"], use_mock_data=False)
    mock = build_connectors(["nado"], use_mock_data=True)

    assert len(real) == 1
    assert real[0].exchange == "nado"
    assert len(mock) == 1
    assert mock[0].exchange == "nado"


def test_nado_real_connector_requires_wallet_address(monkeypatch, tmp_path: Path) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("CREDENTIAL_STORE_PATH", str(tmp_path / "credentials.json"))
    monkeypatch.setenv("NADO_WALLET_ADDRESS", "")

    with pytest.raises(RealConnectorNotConfiguredError, match="NADO_WALLET_ADDRESS"):
        asyncio.run(NadoRealConnector().fetch_account_snapshot())

    get_settings.cache_clear()


def test_nado_real_connector_maps_cross_and_isolated_account(monkeypatch, tmp_path: Path) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    store_path = tmp_path / "credentials.json"
    monkeypatch.setenv("CREDENTIAL_STORE_PATH", str(store_path))
    store = CredentialStore(store_path)
    store.set_exchange_credentials(
        "nado:main",
        {
            "wallet_address": "0x7a5ec2748e9065794491a8d29dcf3f9edb8d7c43",
            "subaccount_name": "default",
        },
    )

    connector = StubNadoConnector()
    connector.exchange = "nado:main"
    connector.account_payload = {
        "status": "success",
        "data": {
            "exists": True,
            "healths": _health(initial=700, maintenance=850, raw=1000),
            "perp_balances": [
                _perp(2, X18 // 2, -50_000 * X18),
                _perp(4, -2 * X18, 8_000 * X18),
                _perp(8, 0, 0),
            ],
        },
    }
    connector.isolated_payload = {
        "status": "success",
        "data": {
            "isolated_positions": [
                {
                    "healths": _health(initial=100, maintenance=150, raw=200),
                    "base_balance": _perp(6, 10 * X18, -1_500 * X18),
                }
            ]
        },
    }
    connector.symbols_payload = [
        {"type": "perp", "product_id": 2, "symbol": "BTC-PERP"},
        {"type": "perp", "product_id": 4, "symbol": "ETH-PERP"},
        {"type": "perp", "product_id": 6, "symbol": "SOL-PERP"},
    ]
    connector.prices_payload = {
        "2": {"product_id": 2, "mark_price_x18": str(110_000 * X18), "update_time": "1"},
        "4": {"product_id": 4, "mark_price_x18": str(3_800 * X18), "update_time": "1"},
        "6": {"product_id": 6, "mark_price_x18": str(160 * X18), "update_time": "1"},
    }

    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert snapshot.exchange == "nado:main"
    assert snapshot.equity_usd == 1200.0
    assert snapshot.available_margin_usd == 800.0
    assert snapshot.maintenance_margin_usd == 200.0
    assert [(p.symbol, p.side, p.size) for p in snapshot.positions] == [
        ("BTC-PERP", "long", 0.5),
        ("ETH-PERP", "short", 2.0),
        ("SOL-PERP", "long", 10.0),
    ]
    assert [(p.entry_price, p.mark_price) for p in snapshot.positions] == [
        (100_000.0, 110_000.0),
        (4_000.0, 3_800.0),
        (150.0, 160.0),
    ]
    assert all(p.leverage == 1.0 for p in snapshot.positions)
    assert all(p.liquidation_price is None for p in snapshot.positions)

    subaccount = _nado_subaccount_hex(
        "0x7a5ec2748e9065794491a8d29dcf3f9edb8d7c43",
        "default",
    )
    headers = {"accept": "application/json", "accept-encoding": "gzip"}
    assert connector.calls == [
        (
            "GET",
            "https://gateway.prod.nado.xyz/v1/query",
            {"type": "subaccount_info", "subaccount": subaccount},
            headers,
        ),
        (
            "GET",
            "https://gateway.prod.nado.xyz/v1/query",
            {"type": "isolated_positions", "subaccount": subaccount},
            headers,
        ),
        ("GET", "https://gateway.prod.nado.xyz/v1/symbols", None, headers),
        (
            "POST",
            "https://archive.prod.nado.xyz/v1",
            {"perp_prices": {"product_ids": [2, 4, 6]}},
            {"content-type": "application/json", "accept": "application/json", "accept-encoding": "gzip"},
        ),
    ]

    get_settings.cache_clear()


def test_nado_real_connector_rejects_missing_account(monkeypatch, tmp_path: Path) -> None:
    from src.config import get_settings

    get_settings.cache_clear()
    store_path = tmp_path / "credentials.json"
    monkeypatch.setenv("CREDENTIAL_STORE_PATH", str(store_path))
    CredentialStore(store_path).set_exchange_credentials(
        "nado",
        {"wallet_address": "0x7a5ec2748e9065794491a8d29dcf3f9edb8d7c43", "subaccount_name": "default"},
    )
    connector = StubNadoConnector()
    connector.account_payload = {"status": "success", "data": {"exists": False}}

    with pytest.raises(RealConnectorRequestError, match="does not exist"):
        asyncio.run(connector.fetch_account_snapshot())

    get_settings.cache_clear()
