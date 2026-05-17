type SummaryCardProps = {
  label: string;
  value: string;
  tone?: 'default' | 'positive' | 'warning' | 'danger';
};

const toneToColor = {
  default: 'var(--text)',
  positive: 'var(--green)',
  warning: 'var(--yellow)',
  danger: 'var(--red)',
} as const;

export function SummaryCard({ label, value, tone = 'default' }: SummaryCardProps) {
  return (
    <div className="summary-card">
      <div className="summary-label">{label}</div>
      <div className="summary-value" style={{ color: toneToColor[tone] }}>
        {value}
      </div>
    </div>
  );
}
