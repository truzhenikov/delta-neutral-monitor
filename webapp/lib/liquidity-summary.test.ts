import test from 'node:test';
import * as assert from 'node:assert/strict';

import { buildLiquiditySummary } from './liquidity-summary';
import type { Account } from './types';

const baseAccount: Account = {
  exchange: 'hyperliquid',
  equity_usd: 10419.06127,
  available_margin_usd: 89.111096,
  maintenance_margin_usd: 1070.35875,
  position_count: 8,
  total_notional_usd: 0,
  total_pnl_usd: 0,
  total_delta_usd: 0,
  load_ratio: 0.10273151280691353,
  updated_at: '2026-05-17T20:00:00.000Z',
  positions: [],
};

test('buildLiquiditySummary returns liquidity metrics for the exchange card', () => {
  const summary = buildLiquiditySummary(baseAccount);

  assert.equal(summary.availableNowUsd, 89.111096);
  assert.equal(summary.bufferUsd, 9348.70252);
  assert.equal(summary.maintenanceUsedUsd, 1070.35875);
  assert.ok(Math.abs(summary.bufferPct - 0.8972691759590737) < 1e-12);
  assert.equal(summary.stressLevel, 'healthy');
});

test('buildLiquiditySummary clamps negative liquidity buffer to zero', () => {
  const stressed = buildLiquiditySummary({
    ...baseAccount,
    equity_usd: 120,
    maintenance_margin_usd: 180,
    load_ratio: 1.5,
  });

  assert.equal(stressed.bufferUsd, 0);
  assert.equal(stressed.bufferPct, 0);
  assert.equal(stressed.stressLevel, 'critical');
});

test('buildLiquiditySummary does not derive stress from available margin when exchanges report it above equity', () => {
  const bitgetStyle = buildLiquiditySummary({
    ...baseAccount,
    equity_usd: 3327.050754170951,
    available_margin_usd: 3970.91667083,
    maintenance_margin_usd: 59.22015681818182,
    load_ratio: 0.0177982237272909,
  });

  assert.equal(bitgetStyle.stressLevel, 'healthy');
  assert.equal(bitgetStyle.bufferUsd, 3267.8305973527693);
  assert.ok(Math.abs(bitgetStyle.bufferPct - 0.9822004047446705) < 1e-12);
});
