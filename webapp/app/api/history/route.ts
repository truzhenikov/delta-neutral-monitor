import { readFile } from 'node:fs/promises';
import { join } from 'node:path';

import { NextResponse } from 'next/server';

const DEFAULT_API_BASE = 'http://127.0.0.1:8080';

async function readDemoHistory() {
  const payloadPath = join(process.cwd(), 'app', 'api', 'history', 'demo-history.json');
  const content = await readFile(payloadPath, 'utf-8');
  return JSON.parse(content);
}

export async function GET() {
  const apiBase = process.env.MONITOR_API_BASE_URL || DEFAULT_API_BASE;

  try {
    const response = await fetch(`${apiBase}/v1/history`, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });

    if (response.ok) {
      const payload = await response.json();
      return NextResponse.json(payload, { status: 200 });
    }
  } catch {
    // Fall back to bundled demo data below.
  }

  const demoPayload = await readDemoHistory();
  return NextResponse.json(demoPayload, { status: 200 });
}
