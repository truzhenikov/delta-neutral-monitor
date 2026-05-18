import { formatMoney } from '@/lib/format';
import type { PortfolioHistoryPayload } from '@/lib/types';

export function HistoryTable({ history }: { history: PortfolioHistoryPayload }) {
  if (history.daily_changes.length === 0) {
    return <div className="empty-state">No day-by-day rows yet.</div>;
  }

  return (
    <div className="history-table-wrap">
      <table className="positions-table history-table">
        <thead>
          <tr>
            <th>Date</th>
            <th>Close Equity</th>
            <th>Day Change</th>
            <th>Warnings</th>
            <th>Summary</th>
          </tr>
        </thead>
        <tbody>
          {history.daily_changes.map((row) => (
            <tr key={row.date}>
              <td>{row.date}</td>
              <td>{formatMoney(row.equity_usd)}</td>
              <td className={row.change_usd !== null && row.change_usd < 0 ? 'negative' : 'positive'}>
                {row.change_usd === null ? '—' : formatMoney(row.change_usd)}
              </td>
              <td>{row.warning_count}</td>
              <td>{row.warnings.length === 0 ? 'Calm session' : row.warnings.join(' · ')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
