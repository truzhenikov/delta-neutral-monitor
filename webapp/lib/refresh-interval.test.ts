import test from 'node:test';
import * as assert from 'node:assert/strict';

import { DEFAULT_REFRESH_INTERVAL_MS, formatRefreshIntervalLabel, normalizeRefreshIntervalMs } from './refresh-interval';

test('normalizeRefreshIntervalMs falls back to default for unsupported values', () => {
  assert.equal(normalizeRefreshIntervalMs(undefined), DEFAULT_REFRESH_INTERVAL_MS);
  assert.equal(normalizeRefreshIntervalMs(12345), DEFAULT_REFRESH_INTERVAL_MS);
});

test('normalizeRefreshIntervalMs preserves supported values including paused mode', () => {
  assert.equal(normalizeRefreshIntervalMs(0), 0);
  assert.equal(normalizeRefreshIntervalMs(30000), 30000);
  assert.equal(normalizeRefreshIntervalMs(60000), 60000);
  assert.equal(normalizeRefreshIntervalMs(300000), 300000);
});

test('formatRefreshIntervalLabel returns readable UI labels', () => {
  assert.equal(formatRefreshIntervalLabel(0), 'Manual only');
  assert.equal(formatRefreshIntervalLabel(30000), '30 sec');
  assert.equal(formatRefreshIntervalLabel(60000), '1 min');
  assert.equal(formatRefreshIntervalLabel(300000), '5 min');
});
