export const REFRESH_INTERVAL_OPTIONS = [0, 30000, 60000, 300000] as const;

export const DEFAULT_REFRESH_INTERVAL_MS = 60000;

export type RefreshIntervalMs = (typeof REFRESH_INTERVAL_OPTIONS)[number];

export function normalizeRefreshIntervalMs(value: number | undefined): RefreshIntervalMs {
  return REFRESH_INTERVAL_OPTIONS.includes(value as RefreshIntervalMs)
    ? (value as RefreshIntervalMs)
    : DEFAULT_REFRESH_INTERVAL_MS;
}

export function formatRefreshIntervalLabel(value: number): string {
  switch (value) {
    case 0:
      return 'Manual only';
    case 30000:
      return '30 sec';
    case 60000:
      return '1 min';
    case 300000:
      return '5 min';
    default:
      return formatRefreshIntervalLabel(DEFAULT_REFRESH_INTERVAL_MS);
  }
}
