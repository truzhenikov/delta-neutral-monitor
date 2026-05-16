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
    <div
      style={{
        background: 'var(--panel)',
        border: '1px solid var(--border)',
        borderRadius: 16,
        padding: 18,
        minHeight: 108,
      }}
    >
      <div style={{ color: 'var(--muted)', fontSize: 13, marginBottom: 10 }}>{label}</div>
      <div style={{ fontSize: 28, fontWeight: 700, color: toneToColor[tone] }}>{value}</div>
    </div>
  );
}
