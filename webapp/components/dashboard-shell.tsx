'use client';

import { CSSProperties, ReactNode, useEffect, useMemo, useState } from 'react';

import { ExchangeCard } from '@/components/exchange-card';
import { SummaryCards } from '@/components/summary-cards';
import { fetchStatus } from '@/lib/api';
import { Account, StatusPayload } from '@/lib/types';

type FilterMode = 'all' | 'with-positions' | 'errors';
type SortMode = 'balance' | 'load' | 'pnl';

export function DashboardShell() {
  const [data, setData] = useState<StatusPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterMode>('all');
  const [sort, setSort] = useState<SortMode>('balance');

  async function load() {
    try {
      setError(null);
      const payload = await fetchStatus();
      setData(payload);
      setLastUpdated(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load dashboard');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
    const interval = setInterval(() => void load(), 15000);
    return () => clearInterval(interval);
  }, []);

  const visibleAccounts = useMemo(() => {
    if (!data) return [];

    const connectorMap = new Map(data.connector_statuses.map((item) => [item.exchange, item]));

    const filtered = data.accounts.filter((account) => {
      if (filter === 'with-positions') return account.position_count > 0;
      if (filter === 'errors') return !connectorMap.get(account.exchange)?.ok;
      return true;
    });

    const sorted = [...filtered].sort((a, b) => {
      if (sort === 'load') return b.load_ratio - a.load_ratio;
      if (sort === 'pnl') return b.total_pnl_usd - a.total_pnl_usd;
      return b.equity_usd - a.equity_usd;
    });

    return sorted;
  }, [data, filter, sort]);

  return (
    <main style={{ maxWidth: 1440, margin: '0 auto', padding: '24px 20px 56px' }}>
      <header style={{ display: 'flex', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap', marginBottom: 24 }}>
        <div>
          <h1 style={{ margin: '0 0 8px', fontSize: 34 }}>Delta Neutral Monitor</h1>
          <div style={{ color: 'var(--muted)' }}>Balances, load, positions, and cross-exchange totals.</div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
          <label style={{ color: 'var(--muted)', fontSize: 14 }}>
            Filter{' '}
            <select value={filter} onChange={(e) => setFilter(e.target.value as FilterMode)} style={selectStyle}>
              <option value="all">All</option>
              <option value="with-positions">Only with positions</option>
              <option value="errors">Only errors</option>
            </select>
          </label>
          <label style={{ color: 'var(--muted)', fontSize: 14 }}>
            Sort{' '}
            <select value={sort} onChange={(e) => setSort(e.target.value as SortMode)} style={selectStyle}>
              <option value="balance">By balance</option>
              <option value="load">By load</option>
              <option value="pnl">By PnL</option>
            </select>
          </label>
          <button onClick={() => void load()} style={buttonStyle}>Refresh</button>
        </div>
      </header>

      {loading ? <Panel><div>Loading dashboard…</div></Panel> : null}
      {error ? <Panel><div style={{ color: 'var(--red)' }}>Failed to load data: {error}</div></Panel> : null}

      {data ? (
        <>
          <SummaryCards data={data} />

          <section style={{ marginTop: 20, display: 'grid', gap: 12 }}>
            <Panel>
              <div style={{ display: 'flex', justifyContent: 'space-between', gap: 12, flexWrap: 'wrap' }}>
                <div>
                  <div style={{ fontSize: 18, fontWeight: 700 }}>Warnings</div>
                  <div style={{ color: 'var(--muted)', marginTop: 6 }}>
                    Last refresh: {lastUpdated || '—'}
                  </div>
                </div>
                <div style={{ color: 'var(--muted)' }}>
                  Connector health: {data.connector_statuses.filter((item) => item.ok).length}/{data.connector_statuses.length} online
                </div>
              </div>
              <ul style={{ marginTop: 12, color: 'var(--text)' }}>
                {data.risk.warnings.length === 0 ? <li>No active warnings.</li> : data.risk.warnings.map((warning) => <li key={warning}>{warning}</li>)}
              </ul>
            </Panel>
          </section>

          <section style={{ marginTop: 20, display: 'grid', gap: 16 }}>
            {visibleAccounts.map((account) => (
              <ExchangeCard
                key={account.exchange}
                account={account}
                connectorStatus={data.connector_statuses.find((item) => item.exchange === account.exchange)}
              />
            ))}
          </section>
        </>
      ) : null}
    </main>
  );
}

function Panel({ children }: { children: ReactNode }) {
  return (
    <div style={{ background: 'var(--panel)', border: '1px solid var(--border)', borderRadius: 16, padding: 18 }}>
      {children}
    </div>
  );
}

const selectStyle: CSSProperties = {
  marginLeft: 8,
  background: 'var(--panel)',
  color: 'var(--text)',
  border: '1px solid var(--border)',
  borderRadius: 10,
  padding: '8px 10px',
};

const buttonStyle: CSSProperties = {
  background: 'var(--blue)',
  border: 'none',
  color: '#07101d',
  fontWeight: 700,
  borderRadius: 10,
  padding: '10px 14px',
  cursor: 'pointer',
};
