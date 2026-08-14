from __future__ import annotations

import asyncio

from src.config import get_settings
from src.connectors.factory import build_connectors
from src.services.credential_store import CredentialStore


def test_lighter_rh_is_supported_by_real_and_mock_factories() -> None:
    real = build_connectors(["lighter-rh"], use_mock_data=False)
    mock = build_connectors(["lighter-rh"], use_mock_data=True)

    assert len(real) == 1
    assert real[0].exchange == "lighter-rh"
    assert len(mock) == 1
    assert mock[0].exchange == "lighter-rh"
    assert CredentialStore.SUPPORTED_EXCHANGES["lighter-rh"] == ("account_index", "l1_address")


def test_lighter_rh_uses_robinhood_chain_api_and_maps_account(monkeypatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("LIGHTER_RH_ACCOUNT_INDEX", "3329")
    monkeypatch.setenv("LIGHTER_RH_L1_ADDRESS", "")
    connector = build_connectors(["lighter-rh"], use_mock_data=False)[0]
    calls: list[tuple[str, str, dict[str, object]]] = []

    async def fake_get(base_url: str, path: str, params=None, headers=None) -> dict:
        calls.append((base_url, path, dict(params or {})))
        return {
            "accounts": [
                {
                    "available_balance": "900.25",
                    "collateral": "996.65",
                    "total_asset_value": "1001.50",
                    "cross_maintenance_margin_requirement": "12.75",
                    "positions": [
                        {
                            "symbol": "LIT",
                            "sign": -1,
                            "position": "100",
                            "avg_entry_price": "2.50",
                            "position_value": "239.00",
                            "allocated_margin": "47.80",
                            "liquidation_price": "3.10",
                        }
                    ],
                }
            ]
        }

    monkeypatch.setattr(connector, "_get", fake_get)
    snapshot = asyncio.run(connector.fetch_account_snapshot())

    assert calls == [
        (
            "https://api.rh.lighter.xyz",
            "/api/v1/account",
            {"by": "index", "value": "3329", "active_only": True},
        )
    ]
    assert snapshot.exchange == "lighter-rh"
    assert snapshot.equity_usd == 1001.50
    assert snapshot.available_margin_usd == 900.25
    assert snapshot.maintenance_margin_usd == 12.75
    assert len(snapshot.positions) == 1
    assert snapshot.positions[0].symbol == "LIT"
    assert snapshot.positions[0].side == "short"
    get_settings.cache_clear()
