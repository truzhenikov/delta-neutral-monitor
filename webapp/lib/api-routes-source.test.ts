import test from 'node:test';
import * as assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

const statusRoutePath = join(process.cwd(), 'app', 'api', 'status', 'route.ts');
const historyRoutePath = join(process.cwd(), 'app', 'api', 'history', 'route.ts');

test('status and history API routes do not reference demo fallback payloads', async () => {
  const [statusRouteSource, historyRouteSource] = await Promise.all([
    readFile(statusRoutePath, 'utf8'),
    readFile(historyRoutePath, 'utf8'),
  ]);

  assert.doesNotMatch(statusRouteSource, /demo/i);
  assert.doesNotMatch(historyRouteSource, /demo/i);
  assert.match(statusRouteSource, /NextResponse\.json\(\{ error:/);
  assert.match(historyRouteSource, /NextResponse\.json\(\{ error:/);
});
