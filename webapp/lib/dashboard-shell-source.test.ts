import test from 'node:test';
import * as assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

const shellPath = join(process.cwd(), 'components', 'dashboard-shell.tsx');

test('dashboard source keeps a single warnings section and does not mention demo fallback', async () => {
  const source = await readFile(shellPath, 'utf8');

  assert.match(source, /Warnings/);
  assert.doesNotMatch(source, /HistoryWarningLog/);
  assert.doesNotMatch(source, /Demo fallback/);
  assert.match(source, /Promise\.allSettled/);
  assert.match(source, /last successfully loaded/i);
  assert.match(source, /stale/i);
  assert.match(source, /history/i);
  assert.match(source, /editorial/i);
});
