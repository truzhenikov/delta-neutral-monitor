import { formatMoney, formatNumber } from '@/lib/format';
import { buildExchangeOverviewRows, buildExchangeOverviewTotals } from '@/lib/exchange-overview';
import type { Account } from '@/lib/types';

export function CompactExchangeOverview({ accounts }: { accounts: Account[] }) {
  const rows = buildExchangeOverviewRows(accounts);
  const totals = buildExchangeOverviewTotals(accounts);

  return (
    <section className="surface-card compact-overview-card">
      <div className="compact-overview-header">
        <div>
          <h2 className="compact-overview-title">Portfolio overview tape</h2>
          <div className="warning-meta">Minimalist table scan of balance, notional volume, and real leverage.</div>
        </div>
        <div className="soft-pill">Table view</div>
      </div>

      <div className="compact-overview-table-wrap">
        <table className="compact-overview-table">
          <thead>
            <tr>
              <th>Exchange</th>
              <th>Balance</th>
              <th>Position Volume</th>
              <th>Real Leverage</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.exchange}>
                <td className="compact-overview-exchange">{row.exchange}</td>
                <td>{formatMoney(row.balanceUsd)}</td>
                <td>{formatMoney(row.positionNotionalUsd)}</td>
                <td>{row.realLeverage === null ? 'Stress' : `${formatNumber(row.realLeverage)}x`}</td>
              </tr>
            ))}
            <tr className="compact-overview-total-row">
              <td className="compact-overview-exchange">{totals.label}</td>
              <td>{formatMoney(totals.balanceUsd)}</td>
              <td>{formatMoney(totals.positionNotionalUsd)}</td>
              <td>{totals.realLeverage === null ? 'Stress' : `${formatNumber(totals.realLeverage)}x`}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  );
}
