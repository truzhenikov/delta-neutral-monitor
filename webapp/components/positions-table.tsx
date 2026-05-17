import { formatMoney, formatNumber } from '@/lib/format';
import { Position } from '@/lib/types';

export function PositionsTable({ positions }: { positions: Position[] }) {
  if (positions.length === 0) {
    return <div className="empty-state" style={{ padding: '8px 0' }}>No open positions.</div>;
  }

  return (
    <div className="positions-table-wrap">
      <table className="positions-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Side</th>
            <th>Size</th>
            <th>Notional</th>
            <th>Entry</th>
            <th>Mark</th>
            <th>Liq</th>
            <th>Lev</th>
            <th>PnL</th>
            <th>Delta</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <tr key={`${position.exchange}-${position.symbol}-${position.side}`}>
              <td>{position.symbol}</td>
              <td style={{ textTransform: 'capitalize' }}>{position.side}</td>
              <td>{formatNumber(position.size)}</td>
              <td>{formatMoney(position.notional_usd)}</td>
              <td>{formatNumber(position.entry_price)}</td>
              <td>{formatNumber(position.mark_price)}</td>
              <td>{position.liquidation_price ? formatNumber(position.liquidation_price) : '—'}</td>
              <td>{formatNumber(position.leverage)}x</td>
              <td className={position.pnl_usd >= 0 ? 'positive' : 'negative'}>{formatMoney(position.pnl_usd)}</td>
              <td className={position.delta_usd >= 0 ? 'positive' : 'negative'}>{formatMoney(position.delta_usd)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
