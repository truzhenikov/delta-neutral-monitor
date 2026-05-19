import type {
  HistoryChartPoint,
  PortfolioHistoryDay,
  PortfolioHistoryPayload,
  PortfolioHistorySnapshot,
} from './types';

export function buildHistoryChartPoints(snapshots: PortfolioHistorySnapshot[]): Array<{ label: string; equityUsd: number; recordedAt: string }> {
  return snapshots.map((snapshot) => ({
    label: new Date(snapshot.recorded_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    equityUsd: snapshot.total_equity_usd,
    recordedAt: snapshot.recorded_at,
  }));
}

export function buildDailyHistoryRows(history: PortfolioHistoryPayload): Array<{
  date: string;
  equityUsd: number;
  changeUsd: number | null;
  warningCount: number;
  warnings: string[];
}> {
  if (history.daily_changes.length > 0) {
    return history.daily_changes.map((row) => ({
      date: row.date,
      equityUsd: row.equity_usd,
      changeUsd: row.change_usd,
      warningCount: row.warning_count,
      warnings: row.warnings,
    }));
  }

  const latestPerDay = new Map<string, PortfolioHistorySnapshot>();
  for (const snapshot of history.snapshots) {
    latestPerDay.set(buildHistoryDayKey(snapshot.recorded_at), snapshot);
  }

  const orderedDays = Array.from(latestPerDay.entries()).sort(([a], [b]) => a.localeCompare(b));
  let previousEquity: number | null = null;
  const rows: Array<{ date: string; equityUsd: number; changeUsd: number | null; warningCount: number; warnings: string[] }> = [];

  for (const [date, snapshot] of orderedDays) {
    rows.push({
      date,
      equityUsd: snapshot.total_equity_usd,
      changeUsd: previousEquity === null ? null : snapshot.total_equity_usd - previousEquity,
      warningCount: snapshot.warning_count,
      warnings: snapshot.warnings,
    });
    previousEquity = snapshot.total_equity_usd;
  }

  return rows.reverse();
}

export function buildHistoryPayloadFromSnapshots(snapshots: PortfolioHistorySnapshot[]): PortfolioHistoryPayload {
  const chart: HistoryChartPoint[] = snapshots.map((snapshot) => ({
    label: new Date(snapshot.recorded_at).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    equity_usd: snapshot.total_equity_usd,
    recorded_at: snapshot.recorded_at,
  }));
  const daily_changes: PortfolioHistoryDay[] = buildDailyHistoryRows({ snapshots, chart: [], daily_changes: [] }).map((row) => ({
    date: row.date,
    equity_usd: row.equityUsd,
    change_usd: row.changeUsd,
    warning_count: row.warningCount,
    warnings: row.warnings,
  }));
  return { snapshots, chart, daily_changes };
}

function buildHistoryDayKey(recordedAt: string): string {
  const timestamp = new Date(recordedAt);
  return new Date(timestamp.getTime() - 2 * 60 * 60 * 1000).toISOString().slice(0, 10);
}
