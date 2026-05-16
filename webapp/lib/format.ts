export function formatMoney(value: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    maximumFractionDigits: 2,
  }).format(value || 0);
}

export function formatNumber(value: number): string {
  return new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 2,
  }).format(value || 0);
}

export function formatPercent(value: number): string {
  return `${(value * 100).toFixed(1)}%`;
}

export function riskColor(level: string): string {
  switch (level) {
    case 'low':
      return 'var(--green)';
    case 'medium':
    case 'high':
      return 'var(--yellow)';
    case 'critical':
      return 'var(--red)';
    default:
      return 'var(--muted)';
  }
}

export function loadColor(loadRatio: number): string {
  if (loadRatio >= 0.6) return 'var(--red)';
  if (loadRatio >= 0.3) return 'var(--yellow)';
  return 'var(--green)';
}
