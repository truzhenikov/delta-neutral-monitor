import { formatMoney } from '@/lib/format';
import type { PortfolioHistoryPayload } from '@/lib/types';

export function HistoryChart({ history }: { history: PortfolioHistoryPayload }) {
  if (history.chart.length === 0) {
    return <div className="empty-state">No history snapshots yet.</div>;
  }

  const width = 760;
  const height = 220;
  const padding = 24;
  const values = history.chart.map((point) => point.equity_usd);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = Math.max(max - min, 1);

  const points = history.chart.map((point, index) => {
    const x = padding + (index / Math.max(history.chart.length - 1, 1)) * (width - padding * 2);
    const y = height - padding - ((point.equity_usd - min) / range) * (height - padding * 2);
    return { ...point, x, y };
  });

  const path = points.map((point, index) => `${index === 0 ? 'M' : 'L'} ${point.x} ${point.y}`).join(' ');
  const area = `${path} L ${points.at(-1)?.x ?? width - padding} ${height - padding} L ${points[0]?.x ?? padding} ${height - padding} Z`;

  return (
    <div className="history-chart-shell">
      <div className="history-chart-header">
        <div>
          <div className="section-eyebrow">Portfolio History</div>
          <h3 className="section-title">Four-hour equity trail</h3>
        </div>
        <div className="history-chart-range">{formatMoney(min)} — {formatMoney(max)}</div>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="history-chart" role="img" aria-label="Portfolio equity history chart">
        <path d={area} className="history-area" />
        <path d={path} className="history-line" />
        {points.map((point) => (
          <g key={point.recorded_at}>
            <circle cx={point.x} cy={point.y} r="4" className="history-dot" />
          </g>
        ))}
      </svg>
      <div className="history-axis-labels">
        {history.chart.map((point) => (
          <span key={point.recorded_at}>{point.label}</span>
        ))}
      </div>
    </div>
  );
}
