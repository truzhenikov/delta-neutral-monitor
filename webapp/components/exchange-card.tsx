'use client';

import { useMemo, useState } from 'react';

import { PositionsTable } from '@/components/positions-table';
import { formatMoney, formatPercent, loadColor } from '@/lib/format';
import { buildLiquiditySummary } from '@/lib/liquidity-summary';
import { Account, ConnectorStatus } from '@/lib/types';

type ExchangeCardProps = {
  account: Account;
  connectorStatus?: ConnectorStatus;
};

export function ExchangeCard({ account, connectorStatus }: ExchangeCardProps) {
  const [open, setOpen] = useState(account.position_count > 0);
  const statusLabel = connectorStatus?.ok ? 'online' : connectorStatus?.error || 'unknown';
  const statusColor = connectorStatus?.ok ? 'var(--green)' : 'var(--red)';
  const statusBackground = connectorStatus?.ok ? 'rgba(24, 195, 126, 0.12)' : 'rgba(242, 95, 92, 0.12)';
  const loadBarColor = loadColor(account.load_ratio);

  const updatedAt = useMemo(() => new Date(account.updated_at).toLocaleString(), [account.updated_at]);
  const liquidity = useMemo(() => buildLiquiditySummary(account), [account]);
  const liquidityTone = liquidityStressTone(liquidity.stressLevel);

  return (
    <article className="exchange-card">
      <div className="exchange-header">
        <div>
          <h3 className="exchange-name">{account.exchange}</h3>
          <div className="exchange-meta">Updated {updatedAt}</div>
        </div>
        <div className="status-pill" style={{ border: `1px solid ${statusColor}`, color: statusColor, background: statusBackground }}>
          {statusLabel}
        </div>
      </div>

      <div className="exchange-metrics">
        <Metric label="Balance" value={formatMoney(account.equity_usd)} />
        <Metric label="Available" value={formatMoney(account.available_margin_usd)} />
        <Metric label="Maintenance" value={formatMoney(account.maintenance_margin_usd)} />
        <Metric label="Positions" value={String(account.position_count)} />
        <Metric label="PnL" value={formatMoney(account.total_pnl_usd)} tone={account.total_pnl_usd >= 0 ? 'var(--green)' : 'var(--red)'} />
        <Metric label="Delta" value={formatMoney(account.total_delta_usd)} tone={account.total_delta_usd >= 0 ? 'var(--green)' : 'var(--red)'} />
      </div>

      <div className="exchange-layers">
        <div className="liquidity-card">
          <div className="liquidity-header">
            <div>
              <div className="liquidity-title">Liquidity Snapshot</div>
              <div className="exchange-meta">Quick buffer summary for this venue</div>
            </div>
            <div className={`liquidity-pill liquidity-pill-${liquidity.stressLevel}`}>{liquidityTone.label}</div>
          </div>

          <div className="liquidity-grid">
            <LiquidityMetric label="Available now" value={formatMoney(liquidity.availableNowUsd)} tone="var(--blue-strong)" />
            <LiquidityMetric label="Maint. used" value={formatMoney(liquidity.maintenanceUsedUsd)} tone="var(--yellow)" />
            <LiquidityMetric label="Buffer" value={formatMoney(liquidity.bufferUsd)} tone={liquidityTone.color} />
            <LiquidityMetric label="Buffer %" value={formatPercent(liquidity.bufferPct)} tone={liquidityTone.color} />
          </div>
        </div>

        <div className="exchange-footer">
          <div className="load-cluster">
            <div className="load-header">
              <span className="section-subtle">Exchange Load</span>
              <span style={{ fontWeight: 700, color: loadBarColor }}>{formatPercent(account.load_ratio)}</span>
            </div>
            <div className="load-track">
              <div className="load-fill" style={{ width: `${Math.min(account.load_ratio * 100, 100)}%`, background: loadBarColor }} />
            </div>
          </div>

          <button onClick={() => setOpen((value) => !value)} className="positions-toggle">
            {open ? 'Hide positions' : 'Show positions'}
          </button>
        </div>
      </div>

      {open ? (
        <div className="positions-wrap">
          <PositionsTable positions={account.positions} />
        </div>
      ) : null}
    </article>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div className="metric-card">
      <div className="metric-label">{label}</div>
      <div className="metric-value" style={{ color: tone || 'var(--text)' }}>
        {value}
      </div>
    </div>
  );
}

function LiquidityMetric({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <div className="liquidity-metric">
      <div className="liquidity-metric-label">{label}</div>
      <div className="liquidity-metric-value" style={{ color: tone }}>
        {value}
      </div>
    </div>
  );
}

function liquidityStressTone(level: 'healthy' | 'watch' | 'tight' | 'critical') {
  switch (level) {
    case 'critical':
      return { color: 'var(--red)', label: 'Critical' };
    case 'tight':
      return { color: 'var(--yellow)', label: 'Tight' };
    case 'watch':
      return { color: 'var(--blue-strong)', label: 'Watch' };
    default:
      return { color: 'var(--green)', label: 'Healthy' };
  }
}
