from __future__ import annotations

import pytest

from src.core.models import AccountSnapshot, Position, utc_now
from src.core.risk import RiskEngine


def test_liquidation_distance_warning_includes_all_breaching_positions() -> None:
    engine = RiskEngine(max_margin_ratio=0.75, min_liq_distance_pct=12.0, max_abs_net_delta_usd=1_000_000.0)
    updated_at = utc_now()

    account = AccountSnapshot(
        exchange="hyperliquid",
        equity_usd=10_000.0,
        available_margin_usd=2_000.0,
        maintenance_margin_usd=800.0,
        updated_at=updated_at,
        positions=[
            Position(
                exchange="hyperliquid",
                symbol="BTC-PERP",
                side="long",
                size=0.1,
                entry_price=100_000.0,
                mark_price=102_000.0,
                leverage=5.0,
                liquidation_price=94_000.0,
            ),
            Position(
                exchange="extended",
                symbol="ETH-PERP",
                side="short",
                size=1.0,
                entry_price=2_500.0,
                mark_price=2_400.0,
                leverage=4.0,
                liquidation_price=2_588.4,
            ),
        ],
    )

    snapshot = engine.evaluate([account])

    assert snapshot.min_liq_distance_pct == pytest.approx(7.8431372549019605)
    assert snapshot.warnings == [
        "Min liquidation distance 7.84% <= threshold 12.00% for hyperliquid BTC-PERP (long)",
        "Min liquidation distance 7.85% <= threshold 12.00% for extended ETH-PERP (short)",
    ]
