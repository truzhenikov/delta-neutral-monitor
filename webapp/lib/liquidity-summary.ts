import type { Account } from './types';

export type LiquidityStressLevel = 'healthy' | 'watch' | 'tight' | 'critical';

export type LiquiditySummary = {
  availableNowUsd: number;
  maintenanceUsedUsd: number;
  bufferUsd: number;
  bufferPct: number;
  stressLevel: LiquidityStressLevel;
};

export function buildLiquiditySummary(account: Account): LiquiditySummary {
  const availableNowUsd = Math.max(account.available_margin_usd || 0, 0);
  const maintenanceUsedUsd = Math.max(account.maintenance_margin_usd || 0, 0);
  const bufferUsd = Math.max((account.equity_usd || 0) - maintenanceUsedUsd, 0);
  const rawBufferPct = account.equity_usd > 0 ? bufferUsd / account.equity_usd : 0;
  const bufferPct = Math.min(Math.max(rawBufferPct, 0), 1);

  return {
    availableNowUsd,
    maintenanceUsedUsd,
    bufferUsd,
    bufferPct,
    stressLevel: resolveStressLevel(bufferPct),
  };
}

function resolveStressLevel(bufferPct: number): LiquidityStressLevel {
  if (bufferPct <= 0.1) return 'critical';
  if (bufferPct <= 0.3) return 'tight';
  if (bufferPct <= 0.5) return 'watch';
  return 'healthy';
}
