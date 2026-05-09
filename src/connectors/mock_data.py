from __future__ import annotations

from src.core.models import AccountSnapshot, Position, utc_now


def make_mock_snapshot(exchange: str, price_shift: float = 0.0) -> AccountSnapshot:
    btc_mark = 103000.0 + price_shift
    eth_mark = 5100.0 + (price_shift / 25)

    positions = [
        Position(
            exchange=exchange,
            symbol="BTCUSDT",
            side="long",
            size=0.15,
            entry_price=100500.0,
            mark_price=btc_mark,
            leverage=5.0,
            liquidation_price=87600.0,
        ),
        Position(
            exchange=exchange,
            symbol="ETHUSDT",
            side="short",
            size=1.8,
            entry_price=5000.0,
            mark_price=eth_mark,
            leverage=4.0,
            liquidation_price=5850.0,
        ),
    ]

    return AccountSnapshot(
        exchange=exchange,
        equity_usd=25000.0 + abs(price_shift) * 0.5,
        available_margin_usd=11000.0,
        maintenance_margin_usd=5200.0,
        positions=positions,
        updated_at=utc_now(),
    )
