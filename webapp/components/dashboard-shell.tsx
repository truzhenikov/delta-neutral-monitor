'use client';

import { ReactNode, useEffect, useMemo, useState } from 'react';

import { CompactExchangeOverview } from '@/components/compact-exchange-overview';
import { ExchangeCard } from '@/components/exchange-card';
import { HistoryChart } from '@/components/history-chart';
import { HistoryTable } from '@/components/history-table';
import { SummaryCards } from '@/components/summary-cards';
import { fetchHistory, fetchStatus } from '@/lib/api';
import { DEFAULT_REFRESH_INTERVAL_MS, formatRefreshIntervalLabel, REFRESH_INTERVAL_OPTIONS, type RefreshIntervalMs } from '@/lib/refresh-interval';
import { PortfolioHistoryPayload, StatusPayload } from '@/lib/types';

type FilterMode = 'all' | 'with-positions' | 'errors';
type SortMode = 'balance' | 'load' | 'pnl';

export function DashboardShell() {
  const [data, setData] = useState<StatusPayload | null>(null);
  const [history, setHistory] = useState<PortfolioHistoryPayload | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastUpdated, setLastUpdated] = useState<string | null>(null);
  const [filter, setFilter] = useState<FilterMode>('all');
  const [sort, setSort] = useState<SortMode>('balance');
  const [refreshIntervalMs, setRefreshIntervalMs] = useState<RefreshIntervalMs>(DEFAULT_REFRESH_INTERVAL_MS);

  async function load() {
    setError(null);
    // Keep rendering the latest successful status/history payload even when one of
    // the background refresh requests fails.
    const [statusResult, historyResult] = await Promise.allSettled([fetchStatus(), fetchHistory()]);

    if (statusResult.status === 'fulfilled') {
      setData(statusResult.value);
      setLastUpdated(new Date().toLocaleTimeString());
    }

    if (historyResult.status === 'fulfilled') {
      setHistory(historyResult.value);
    }

    const errors = [statusResult, historyResult]
      .filter((result): result is PromiseRejectedResult => result.status === 'rejected')
      .map((result) => result.reason instanceof Error ? result.reason.message : 'Failed to load dashboard');

    if (errors.length > 0) {
      setError(errors.join(' · '));
    }

    setLoading(false);
  }

  useEffect(() => {
    void load();

    if (refreshIntervalMs === 0) {
      return;
    }

    const interval = setInterval(() => void load(), refreshIntervalMs);
    return () => clearInterval(interval);
  }, [refreshIntervalMs]);

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

    return [...filtered].sort((a, b) => {
      if (sort === 'load') return b.load_ratio - a.load_ratio;
      if (sort === 'pnl') return b.total_pnl_usd - a.total_pnl_usd;
      return b.equity_usd - a.equity_usd;
    });
  }, [data, filter, sort]);

  const staleExchanges = useMemo(() => new Set(
    data?.connector_statuses.filter((item) => !item.ok).map((item) => item.exchange) ?? [],
  ), [data]);

  const staleExchangeLabel = useMemo(() => Array.from(staleExchanges).join(', '), [staleExchanges]);
  const snapshotUpdatedAt = data ? new Date(data.snapshot_updated_at).toLocaleString() : null;

  return (
    <main className="dashboard-page editorial-dashboard">
      <section className="dashboard-hero editorial-hero">
        <div className="dashboard-hero-card hero-copy-card">
          <div className="section-eyebrow">Editorial risk briefing</div>
          <h1 className="dashboard-title">Delta Neutral Monitor</h1>
          <div className="dashboard-subtitle">
            A cleaner editorial view of balances, warnings, portfolio history, margin load, and venue-level positioning across the landing dashboard.
          </div>

          <div className="hero-badges">
            <div className="hero-badge">Refresh cadence <strong>{formatRefreshIntervalLabel(refreshIntervalMs)}</strong></div>
            <div className="hero-badge">Connectors online <strong>{connectorHealth.ok}/{connectorHealth.total || '—'}</strong></div>
            <div className="hero-badge">Last refresh <strong>{lastUpdated || '—'}</strong></div>
            <div className="hero-badge">Snapshot <strong>{data?.source === 'stale' ? 'Stale cached data' : 'Live data'}</strong></div>
          </div>
        </div>

        <div className="control-card editorial-control-card">
          <div>
            <div className="control-card-title">View controls</div>
            <div className="control-grid">
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
              <label className="control-label">
                <span>Auto refresh</span>
                <select value={refreshIntervalMs} onChange={(e) => setRefreshIntervalMs(Number(e.target.value) as RefreshIntervalMs)} className="dashboard-select">
                  {REFRESH_INTERVAL_OPTIONS.map((value) => (
                    <option key={value} value={value}>{formatRefreshIntervalLabel(value)}</option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          <div className="control-stack">
            <button onClick={() => void load()} className="dashboard-button">Refresh now</button>
          </div>
        </div>
      </section>

      {loading ? <Panel><div>Loading dashboard…</div></Panel> : null}
      {error ? <Panel><div className="negative">Failed to load data: {error}{data ? ' Showing the last successfully loaded dashboard snapshot.' : ''}</div></Panel> : null}

      {data ? (
        <>
          {data.source === 'stale' ? (
            <section className="dashboard-section">
              <Panel warning>
                <div className="negative">
                  Some exchanges failed during the latest refresh. Showing cached data snapshot from <strong>{snapshotUpdatedAt || '—'}</strong>
                  {staleExchangeLabel ? <> for: <strong>{staleExchangeLabel}</strong></> : null}.
                </div>
              </Panel>
            </section>
          ) : null}

          <SummaryCards data={data} />

          <section className="dashboard-section">
            <div className="section-heading-row">
              <div>
                <div className="section-eyebrow">Overview</div>
                <h2 className="section-title">Current portfolio posture</h2>
              </div>
              <div className="soft-pill">Snapshot as of {snapshotUpdatedAt || '—'}</div>
            </div>
            <CompactExchangeOverview accounts={visibleAccounts} staleExchanges={staleExchanges} />
          </section>

          <section className="dashboard-section">
            <Panel warning>
              <div className="warning-header">
                <div className="warning-title">
                  <div className="section-eyebrow">Live warnings</div>
                  <h2>Warnings</h2>
                  <div className="warning-meta">Current risk flags across the portfolio.</div>
                </div>
                <div className="health-pill">Connector health {connectorHealth.ok}/{connectorHealth.total}</div>
              </div>
              <ul className="warning-list">
                {data.risk.warnings.length === 0 ? <li>No active warnings.</li> : data.risk.warnings.map((warning) => <li key={warning}>{warning}</li>)}
              </ul>
            </Panel>
          </section>

          {history ? (
            <section className="dashboard-section history-section">
              <div className="section-heading-row">
                <div>
                  <div className="section-eyebrow">History</div>
                  <h2 className="section-title">Portfolio history</h2>
                </div>
                <div className="soft-pill">{history.snapshots.length} stored snapshots</div>
              </div>
              <HistoryChart history={history} />
              <div className="surface-card">
                <div className="section-eyebrow">Day by day</div>
                <h3 className="section-title">Daily equity change table</h3>
                <div className="section-subtle">Boundary: 05:00 MSK.</div>
                <HistoryTable history={history} />
              </div>
            </section>
          ) : null}

          <section className="exchange-grid dashboard-section">
            <div className="section-heading-row">
              <div>
                <div className="section-eyebrow">Venues</div>
                <h2 className="section-title">Exchange detail cards</h2>
              </div>
              <div className="soft-pill">{visibleAccounts.length} exchanges in view</div>
            </div>
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
