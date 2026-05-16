from __future__ import annotations

from src.core.models import AccountSnapshot, ConnectorStatus
from src.core.risk import RiskEngine
from src.core.schemas import AccountOut, ConnectorStatusOut, PositionOut, RiskOut, StatusOut


class StatusService:
    def __init__(self, risk_engine: RiskEngine) -> None:
        self.risk_engine = risk_engine

    def build_status(
        self,
        accounts: list[AccountSnapshot],
        connector_statuses: list[ConnectorStatus] | None = None,
    ) -> StatusOut:
        risk = self.risk_engine.evaluate(accounts)

        total_equity = sum(a.equity_usd for a in accounts)
        total_available = sum(a.available_margin_usd for a in accounts)
        total_maintenance = sum(a.maintenance_margin_usd for a in accounts)

        account_out: list[AccountOut] = []
        for acc in accounts:
            positions = [
                PositionOut(
                    exchange=p.exchange,
                    symbol=p.symbol,
                    side=p.side,
                    size=p.size,
                    entry_price=p.entry_price,
                    mark_price=p.mark_price,
                    leverage=p.leverage,
                    liquidation_price=p.liquidation_price,
                    notional_usd=p.notional_usd,
                    pnl_usd=p.pnl_usd,
                    delta_usd=p.delta_usd,
                )
                for p in acc.positions
            ]
            total_notional = sum(p.notional_usd for p in acc.positions)
            total_pnl = sum(p.pnl_usd for p in acc.positions)
            total_delta = sum(p.delta_usd for p in acc.positions)
            load_ratio = (acc.maintenance_margin_usd / acc.equity_usd) if acc.equity_usd else 0.0
            account_out.append(
                AccountOut(
                    exchange=acc.exchange,
                    equity_usd=acc.equity_usd,
                    available_margin_usd=acc.available_margin_usd,
                    maintenance_margin_usd=acc.maintenance_margin_usd,
                    position_count=len(acc.positions),
                    total_notional_usd=total_notional,
                    total_pnl_usd=total_pnl,
                    total_delta_usd=total_delta,
                    load_ratio=load_ratio,
                    updated_at=acc.updated_at,
                    positions=positions,
                )
            )

        connector_out = [
            ConnectorStatusOut(
                exchange=s.exchange,
                ok=s.ok,
                error=s.error,
                updated_at=s.updated_at,
            )
            for s in (connector_statuses or [])
        ]

        return StatusOut(
            total_equity_usd=total_equity,
            total_available_margin_usd=total_available,
            total_maintenance_margin_usd=total_maintenance,
            accounts=account_out,
            connector_statuses=connector_out,
            risk=RiskOut(
                net_delta_usd=risk.net_delta_usd,
                margin_ratio=risk.margin_ratio,
                min_liq_distance_pct=risk.min_liq_distance_pct,
                risk_level=risk.risk_level,
                warnings=risk.warnings,
                generated_at=risk.generated_at,
            ),
        )
