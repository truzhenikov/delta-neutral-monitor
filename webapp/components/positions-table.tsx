import { formatMoney, formatNumber } from '@/lib/format';
import { Position } from '@/lib/types';

export function PositionsTable({ positions }: { positions: Position[] }) {
  if (positions.length === 0) {
    return <div style={{ color: 'var(--muted)', padding: '8px 0' }}>No open positions.</div>;
  }

  return (
    <div style={{ overflowX: 'auto' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ color: 'var(--muted)', fontSize: 12, textAlign: 'left' }}>
            <th style={{ padding: '10px 8px' }}>Symbol</th>
            <th style={{ padding: '10px 8px' }}>Side</th>
            <th style={{ padding: '10px 8px' }}>Size</th>
            <th style={{ padding: '10px 8px' }}>Notional</th>
            <th style={{ padding: '10px 8px' }}>Entry</th>
            <th style={{ padding: '10px 8px' }}>Mark</th>
            <th style={{ padding: '10px 8px' }}>Liq</th>
            <th style={{ padding: '10px 8px' }}>Lev</th>
            <th style={{ padding: '10px 8px' }}>PnL</th>
            <th style={{ padding: '10px 8px' }}>Delta</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <tr key={`${position.exchange}-${position.symbol}-${position.side}`} style={{ borderTop: '1px solid var(--border)' }}>
              <td style={{ padding: '10px 8px' }}>{position.symbol}</td>
              <td style={{ padding: '10px 8px', textTransform: 'capitalize' }}>{position.side}</td>
              <td style={{ padding: '10px 8px' }}>{formatNumber(position.size)}</td>
              <td style={{ padding: '10px 8px' }}>{formatMoney(position.notional_usd)}</td>
              <td style={{ padding: '10px 8px' }}>{formatNumber(position.entry_price)}</td>
              <td style={{ padding: '10px 8px' }}>{formatNumber(position.mark_price)}</td>
              <td style={{ padding: '10px 8px' }}>{position.liquidation_price ? formatNumber(position.liquidation_price) : '—'}</td>
              <td style={{ padding: '10px 8px' }}>{formatNumber(position.leverage)}x</td>
              <td style={{ padding: '10px 8px', color: position.pnl_usd >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {formatMoney(position.pnl_usd)}
              </td>
              <td style={{ padding: '10px 8px', color: position.delta_usd >= 0 ? 'var(--green)' : 'var(--red)' }}>
                {formatMoney(position.delta_usd)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
