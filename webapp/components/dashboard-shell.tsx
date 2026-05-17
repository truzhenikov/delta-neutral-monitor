'use client';

import { ReactNode, useEffect, useMemo, useState } from 'react';

import { ExchangeCard } from '@/components/exchange-card';
import { SummaryCards } from '@/components/summary-cards';
import { fetchStatus } from '@/lib/api';
import { StatusPayload } from '@/lib/types';

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

  const connectorHealth = useMemo(() => {
    if (!data) return { ok: 0, total: 0 };
    return {
      ok: data.connector_statuses.filter((item) => item.ok).length,
      total: data.connector_statuses.length,
    };
  }, [data]);

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
    <main className="dashboard-page">
      <section className="dashboard-hero">
        <div className="dashboard-hero-card">
          <h1 className="dashboard-title">Delta Neutral Monitor</h1>
          <div className="dashboard-subtitle">
            Cross-exchange view of balances, margin usage, directional exposure, and open positions across the portfolio.
          </div>

          <div className="hero-badges">
            <div className="hero-badge">
              Refresh cadence: <strong>15 sec</strong>
            </div>
            <div className="hero-badge">
              Connectors online: <strong>{connectorHealth.ok}/{connectorHealth.total || '—'}</strong>
            </div>
            <div className="hero-badge">
              Last refresh: <strong>{lastUpdated || '—'}</strong>
            </div>
          </div>
        </div>

        <div className="control-card">
          <div>
            <div className="control-card-title">View controls</div>
            <div className="control-grid" style={{ marginTop: 14 }}>
              <label className="control-label">
                <span>Filter</span>
                <select value={filter} onChange={(e) => setFilter(e.target.value as FilterMode)} className="dashboard-select">
                  <option value="all">All exchanges</option>
                  <option value="with-positions">Only with positions</option>
                  <option value="errors">Only errors</option>
                </select>
              </label>
              <label className="control-label">
                <span>Sort</span>
                <select value={sort} onChange={(e) => setSort(e.target.value as SortMode)} className="dashboard-select">
                  <option value="balance">By balance</option>
                  <option value="load">By load</option>
                  <option value="pnl">By PnL</option>
                </select>
              </label>
            </div>
          </div>

          <div style={{ display: 'grid', gap: 12 }}>
            <button onClick={() => void load()} className="dashboard-button">Refresh now</button>
            <div className="control-caption">Use filters to isolate stressed exchanges, then sort by balance, load, or current PnL to scan risk faster.</div>
          </div>
        </div>
      </section>

      {loading ? <Panel><div>Loading dashboard…</div></Panel> : null}
      {error ? <Panel><div style={{ color: 'var(--red)' }}>Failed to load data: {error}</div></Panel> : null}

      {data ? (
        <>
          <SummaryCards data={data} />

          <section style={{ marginTop: 20, display: 'grid', gap: 12 }}>
            <Panel warning>
              <div className="warning-header">
                <div className="warning-title">
                  <h2>Warnings</h2>
                  <div className="warning-meta">Watchlist generated from current margin usage, liquidation distance, and net exposure.</div>
                </div>
                <div className="health-pill">Connector health {connectorHealth.ok}/{connectorHealth.total}</div>
              </div>

              <ul className="warning-list">
                {data.risk.warnings.length === 0 ? <li>No active warnings.</li> : data.risk.warnings.map((warning) => <li key={warning}>{warning}</li>)}
              </ul>
            </Panel>
          </section>

          <section className="exchange-grid">
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

function Panel({ children, warning = false }: { children: ReactNode; warning?: boolean }) {
  return <div className={`surface-card${warning ? ' warning-card' : ''}`}>{children}</div>;
}
