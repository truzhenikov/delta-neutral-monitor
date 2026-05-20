import { NextResponse } from 'next/server';

import { normalizeApiBase } from '@/lib/api-base';

export async function GET() {
  const apiBase = normalizeApiBase(process.env.MONITOR_API_BASE_URL);

  try {
    const response = await fetch(`${apiBase}/v1/status`, {
      cache: 'no-store',
      headers: { Accept: 'application/json' },
    });

    if (response.ok) {
      const payload = await response.json();
      return NextResponse.json(payload, { status: 200 });
    }

    const errorText = await response.text();
    return NextResponse.json(
      {
        error: `Backend status request failed with ${response.status}${errorText ? `: ${errorText.slice(0, 300)}` : ''}`,
      },
      { status: 502 },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown backend status error';
    return NextResponse.json({ error: `Backend status request failed: ${message}` }, { status: 502 });
  }
}
