import test from 'node:test';
import * as assert from 'node:assert/strict';

import { buildDailyHistoryRows, buildHistoryChartPoints } from './history';
import type { PortfolioHistoryPayload } from './types';

const sampleHistory: PortfolioHistoryPayload = {
  snapshots: [
    {
      recorded_at: '2026-05-17T00:00:00.000Z',
      total_equity_usd: 1000,
      total_available_margin_usd: 600,
      total_maintenance_margin_usd: 120,
      warning_count: 0,
      warnings: [],
    },
    {
      recorded_at: '2026-05-17T20:00:00.000Z',
      total_equity_usd: 1080,
      total_available_margin_usd: 640,
      total_maintenance_margin_usd: 130,
      warning_count: 1,
      warnings: ['Margin ratio 0.81 >= threshold 0.75'],
    },
    {
      recorded_at: '2026-05-18T04:00:00.000Z',
      total_equity_usd: 980,
      total_available_margin_usd: 510,
      total_maintenance_margin_usd: 150,
      warning_count: 2,
      warnings: ['Margin ratio 0.82 >= threshold 0.75', 'Net delta 900.00 USD exceeds threshold 500.00'],
    },
  ],
  chart: [],
  daily_changes: [],
};

test('buildHistoryChartPoints covers all snapshots', () => {
  const chart = buildHistoryChartPoints(sampleHistory.snapshots);

  assert.equal(chart.length, sampleHistory.snapshots.length);
  assert.equal(chart[0]?.label, 'May 17');
  assert.equal(chart[2]?.equityUsd, 980);
});

test('buildDailyHistoryRows groups latest snapshot per day and carries warnings', () => {
  const rows = buildDailyHistoryRows(sampleHistory);

  assert.equal(rows.length, 2);
  assert.equal(rows[0]?.date, '2026-05-18');
  assert.equal(rows[0]?.warningCount, 2);
  assert.match(rows[0]?.warnings[0] ?? '', /Margin ratio/);
  assert.equal(rows[0]?.changeUsd, -100);
  assert.equal(rows[1]?.changeUsd, null);
});
