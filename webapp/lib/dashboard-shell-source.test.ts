import test from 'node:test';
import * as assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

const shellPath = join(process.cwd(), 'components', 'dashboard-shell.tsx');

test('dashboard source preserves warnings section and history section markers', async () => {
  const source = await readFile(shellPath, 'utf8');

  assert.match(source, /Warnings/);
  assert.match(source, /history/i);
  assert.match(source, /editorial/i);
});
