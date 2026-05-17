import { formatMoney, formatNumber } from '@/lib/format';
import { buildExchangeOverviewRows } from '@/lib/exchange-overview';
import type { Account } from '@/lib/types';

export function CompactExchangeOverview({ accounts }: { accounts: Account[] }) {
  const rows = buildExchangeOverviewRows(accounts);

  return (
    <section className="surface-card compact-overview-card">
      <div className="compact-overview-header">
        <div>
          <h2 className="compact-overview-title">Exchange Quick View</h2>
          <div className="warning-meta">Compact scan of venue balance, position size, and real leverage.</div>
        </div>
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
          </tbody>
        </table>
      </div>
    </section>
  );
}
