import { NextResponse } from 'next/server';

const DEFAULT_API_BASE = 'http://127.0.0.1:8080';

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

    const errorText = await response.text();
    return NextResponse.json(
      {
        error: `Backend history request failed with ${response.status}${errorText ? `: ${errorText.slice(0, 300)}` : ''}`,
      },
      { status: 502 },
    );
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Unknown backend history error';
    return NextResponse.json({ error: `Backend history request failed: ${message}` }, { status: 502 });
  }
}
