'use client';

import { useMemo, useState } from 'react';

import { PositionsTable } from '@/components/positions-table';
import { formatMoney, formatPercent, loadColor } from '@/lib/format';
import { Account, ConnectorStatus } from '@/lib/types';

type ExchangeCardProps = {
  account: Account;
  connectorStatus?: ConnectorStatus;
};

export function ExchangeCard({ account, connectorStatus }: ExchangeCardProps) {
  const [open, setOpen] = useState(account.position_count > 0);
  const statusLabel = connectorStatus?.ok ? 'online' : connectorStatus?.error || 'unknown';
  const statusColor = connectorStatus?.ok ? 'var(--green)' : 'var(--red)';
  const loadBarColor = loadColor(account.load_ratio);

  const updatedAt = useMemo(() => new Date(account.updated_at).toLocaleString(), [account.updated_at]);

  return (
    <article
      style={{
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        borderRadius: 18,
        padding: 18,
        display: 'flex',
        flexDirection: 'column',
        gap: 16,
      }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, alignItems: 'start' }}>
        <div>
          <div style={{ fontSize: 22, fontWeight: 700, textTransform: 'capitalize' }}>{account.exchange}</div>
          <div style={{ color: 'var(--muted)', fontSize: 13 }}>Updated {updatedAt}</div>
        </div>
        <div
          style={{
            border: `1px solid ${statusColor}`,
            color: statusColor,
            borderRadius: 999,
            padding: '6px 10px',
            fontSize: 12,
            textTransform: 'uppercase',
          }}
        >
          {statusLabel}
        </div>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
          gap: 12,
        }}
      >
        <Metric label="Balance" value={formatMoney(account.equity_usd)} />
        <Metric label="Available" value={formatMoney(account.available_margin_usd)} />
        <Metric label="Maintenance" value={formatMoney(account.maintenance_margin_usd)} />
        <Metric label="Positions" value={String(account.position_count)} />
        <Metric label="PnL" value={formatMoney(account.total_pnl_usd)} tone={account.total_pnl_usd >= 0 ? 'var(--green)' : 'var(--red)'} />
        <Metric label="Delta" value={formatMoney(account.total_delta_usd)} tone={account.total_delta_usd >= 0 ? 'var(--green)' : 'var(--red)'} />
      </div>

      <div>
        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 8 }}>
          <span style={{ color: 'var(--muted)', fontSize: 13 }}>Exchange Load</span>
          <span style={{ fontWeight: 600, color: loadBarColor }}>{formatPercent(account.load_ratio)}</span>
        </div>
        <div style={{ height: 10, background: 'var(--panel-alt)', borderRadius: 999, overflow: 'hidden' }}>
          <div style={{ width: `${Math.min(account.load_ratio * 100, 100)}%`, height: '100%', background: loadBarColor }} />
        </div>
      </div>

      <div>
        <button
          onClick={() => setOpen((value) => !value)}
          style={{
            background: 'transparent',
            color: 'var(--blue)',
            border: 'none',
            padding: 0,
            cursor: 'pointer',
            fontWeight: 600,
          }}
        >
          {open ? 'Hide positions' : 'Show positions'}
        </button>
        {open ? <div style={{ marginTop: 12 }}><PositionsTable positions={account.positions} /></div> : null}
      </div>
    </article>
  );
}

function Metric({ label, value, tone }: { label: string; value: string; tone?: string }) {
  return (
    <div style={{ background: 'var(--panel-alt)', borderRadius: 12, padding: 12 }}>
      <div style={{ color: 'var(--muted)', fontSize: 12, marginBottom: 6 }}>{label}</div>
      <div style={{ fontWeight: 700, color: tone || 'var(--text)' }}>{value}</div>
    </div>
  );
}
