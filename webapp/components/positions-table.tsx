import { formatMoney, formatNumber } from '@/lib/format';
import { calculateLiqDistancePct } from '@/lib/position-metrics';
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
            <th>To liq</th>
            <th>Lev</th>
            <th>PnL</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => {
            const liqDistancePct = calculateLiqDistancePct({
              side: position.side,
              markPrice: position.mark_price,
              liquidationPrice: position.liquidation_price,
            });

            return (
              <tr key={`${position.exchange}-${position.symbol}-${position.side}`}>
                <td>{position.symbol}</td>
                <td style={{ textTransform: 'capitalize' }}><span className="soft-pill">{position.side}</span></td>
                <td>{formatNumber(position.size)}</td>
                <td>{formatMoney(position.notional_usd)}</td>
                <td>{formatNumber(position.entry_price)}</td>
                <td>{formatNumber(position.mark_price)}</td>
                <td>{position.liquidation_price ? formatNumber(position.liquidation_price) : '—'}</td>
                <td className={liqDistancePct !== null && liqDistancePct <= 12 ? 'negative' : undefined}>{liqDistancePct === null ? '—' : `${liqDistancePct.toFixed(2)}%`}</td>
                <td>{formatNumber(position.leverage)}x</td>
                <td className={position.pnl_usd >= 0 ? 'positive' : 'negative'}>{formatMoney(position.pnl_usd)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
