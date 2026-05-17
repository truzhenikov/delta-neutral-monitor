import { SummaryCard } from '@/components/summary-card';
import { formatMoney, formatNumber } from '@/lib/format';
import { StatusPayload } from '@/lib/types';

export function SummaryCards({ data }: { data: StatusPayload }) {
  const activeExchanges = data.connector_statuses.filter((item) => item.ok).length;
  const failedExchanges = data.connector_statuses.filter((item) => !item.ok).length;

  return (
    <section className="summary-grid">
      <SummaryCard label="All Exchanges Balance" value={formatMoney(data.total_equity_usd)} />
      <SummaryCard label="Available Margin" value={formatMoney(data.total_available_margin_usd)} />
      <SummaryCard label="Maintenance Margin" value={formatMoney(data.total_maintenance_margin_usd)} />
      <SummaryCard label="Net Delta" value={formatMoney(data.risk.net_delta_usd)} />
      <SummaryCard
        label="Global Risk"
        value={data.risk.risk_level.toUpperCase()}
        tone={data.risk.risk_level === 'critical' ? 'danger' : data.risk.risk_level === 'low' ? 'positive' : 'warning'}
      />
      <SummaryCard label="Active / Failed" value={`${formatNumber(activeExchanges)} / ${formatNumber(failedExchanges)}`} />
    </section>
  );
}
