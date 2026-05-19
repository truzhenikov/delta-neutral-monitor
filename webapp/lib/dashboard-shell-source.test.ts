import test from 'node:test';
import * as assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

const shellPath = join(process.cwd(), 'components', 'dashboard-shell.tsx');

test('dashboard source keeps a single warnings section and does not render the historical warning log card', async () => {
  const source = await readFile(shellPath, 'utf8');

  assert.match(source, /Warnings/);
  assert.doesNotMatch(source, /HistoryWarningLog/);
  assert.match(source, /history/i);
  assert.match(source, /editorial/i);
});
