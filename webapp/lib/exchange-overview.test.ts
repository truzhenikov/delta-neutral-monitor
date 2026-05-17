import test from 'node:test';
import * as assert from 'node:assert/strict';

import { buildExchangeOverviewRows } from './exchange-overview';
import type { Account } from './types';

const accounts: Account[] = [
  {
    exchange: 'bitget',
    equity_usd: 3190.055754170951,
    available_margin_usd: 0,
    maintenance_margin_usd: 0,
    position_count: 3,
    total_notional_usd: 33435.075000000004,
    total_pnl_usd: 0,
    total_delta_usd: 0,
    load_ratio: 0,
    updated_at: '2026-05-17T20:00:00.000Z',
    positions: [],
  },
  {
    exchange: 'kucoin',
    equity_usd: 2424.653232496,
    available_margin_usd: 0,
    maintenance_margin_usd: 0,
    position_count: 2,
    total_notional_usd: 6932.639999999999,
    total_pnl_usd: 0,
    total_delta_usd: 0,
    load_ratio: 0,
    updated_at: '2026-05-17T20:00:00.000Z',
    positions: [],
  },
  {
    exchange: 'stress',
    equity_usd: -150,
    available_margin_usd: 0,
    maintenance_margin_usd: 0,
    position_count: 1,
    total_notional_usd: 1200,
    total_pnl_usd: 0,
    total_delta_usd: 0,
    load_ratio: 0,
    updated_at: '2026-05-17T20:00:00.000Z',
    positions: [],
  },
  {
    exchange: 'empty',
    equity_usd: 0,
    available_margin_usd: 0,
    maintenance_margin_usd: 0,
    position_count: 0,
    total_notional_usd: 0,
    total_pnl_usd: 0,
    total_delta_usd: 0,
    load_ratio: 0,
    updated_at: '2026-05-17T20:00:00.000Z',
    positions: [],
  },
];

test('buildExchangeOverviewRows returns compact metrics including real leverage', () => {
  const rows = buildExchangeOverviewRows(accounts);

  assert.deepEqual(rows.map((row) => row.exchange), ['bitget', 'kucoin', 'stress', 'empty']);
  assert.equal(rows[0]?.balanceUsd, 3190.055754170951);
  assert.equal(rows[0]?.positionNotionalUsd, 33435.075000000004);
  assert.ok(Math.abs((rows[0]?.realLeverage ?? 0) - 10.481031548205431) < 1e-12);
  assert.ok(Math.abs((rows[1]?.realLeverage ?? 0) - 2.859229479534013) < 1e-12);
});

test('buildExchangeOverviewRows returns zero leverage when exchange equity is zero', () => {
  const rows = buildExchangeOverviewRows(accounts);
  const empty = rows.find((row) => row.exchange === 'empty');

  assert.equal(empty?.positionNotionalUsd, 0);
  assert.equal(empty?.realLeverage, 0);
});

test('buildExchangeOverviewRows flags stressed exchanges when notional exists but equity is non-positive', () => {
  const rows = buildExchangeOverviewRows(accounts);
  const stress = rows.find((row) => row.exchange === 'stress');

  assert.equal(stress?.positionNotionalUsd, 1200);
  assert.equal(stress?.realLeverage, null);
});
