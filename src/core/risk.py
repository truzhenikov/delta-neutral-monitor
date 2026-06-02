from __future__ import annotations

from src.core.models import AccountSnapshot, RiskSnapshot, utc_now


class RiskEngine:
    def __init__(
        self,
        max_margin_ratio: float,
        min_liq_distance_pct: float,
        max_abs_net_delta_usd: float,
    ) -> None:
        self.max_margin_ratio = max_margin_ratio
        self.min_liq_distance_pct = min_liq_distance_pct
        self.max_abs_net_delta_usd = max_abs_net_delta_usd

    def evaluate(self, accounts: list[AccountSnapshot]) -> RiskSnapshot:
        total_equity = sum(a.equity_usd for a in accounts)
        total_maintenance = sum(a.maintenance_margin_usd for a in accounts)

        margin_ratio = (total_maintenance / total_equity) if total_equity > 0 else 1.0

        positions = [p for a in accounts for p in a.positions]
        net_delta = sum(p.delta_usd for p in positions)

        liq_distances: list[tuple[float, str]] = []
        liq_breaches: list[tuple[float, str]] = []
        for p in positions:
            if p.liquidation_price is None or p.mark_price <= 0:
                continue
            if p.side == "long":
                dist = (p.mark_price - p.liquidation_price) / p.mark_price * 100
            else:
                dist = (p.liquidation_price - p.mark_price) / p.mark_price * 100
            context = f"{p.exchange} {p.symbol} ({p.side})"
            liq_distances.append((dist, context))
            if dist <= self.min_liq_distance_pct:
                liq_breaches.append((dist, context))

        min_liq_dist = min((dist for dist, _ in liq_distances), default=None)

        warnings: list[str] = []
        if margin_ratio >= self.max_margin_ratio:
            warnings.append(
                f"Margin ratio {margin_ratio:.2f} >= threshold {self.max_margin_ratio:.2f}"
            )
        if abs(net_delta) >= self.max_abs_net_delta_usd:
            warnings.append(
                f"Net delta {net_delta:.2f} USD exceeds threshold {self.max_abs_net_delta_usd:.2f}"
            )
        for dist, context in sorted(liq_breaches, key=lambda item: item[0]):
            warnings.append(
                f"Min liquidation distance {dist:.2f}% <= threshold {self.min_liq_distance_pct:.2f}% for {context}"
            )

        risk_level = "low"
        if len(warnings) == 1:
            risk_level = "medium"
        elif len(warnings) == 2:
            risk_level = "high"
        elif len(warnings) >= 3:
            risk_level = "critical"

        return RiskSnapshot(
            net_delta_usd=net_delta,
            margin_ratio=margin_ratio,
            min_liq_distance_pct=min_liq_dist,
            risk_level=risk_level,
            warnings=warnings,
            generated_at=utc_now(),
        )
