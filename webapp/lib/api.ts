import { PortfolioHistoryPayload, StatusPayload } from '@/lib/types';

export async function fetchStatus(): Promise<StatusPayload> {
  const response = await fetch('/api/status', {
    cache: 'no-store',
  });

  if (!response.ok) {
    let message = `Request failed with ${response.status}`;
    try {
      const payload = (await response.json()) as { error?: string };
      if (payload.error) {
        message = payload.error;
      }
    } catch {
      // ignore JSON parsing issues
    }
    throw new Error(message);
  }

  return (await response.json()) as StatusPayload;
}

export async function fetchHistory(): Promise<PortfolioHistoryPayload> {
  const response = await fetch('/api/history', {
    cache: 'no-store',
  });

  if (!response.ok) {
    let message = `Request failed with ${response.status}`;
    try {
      const payload = (await response.json()) as { error?: string };
      if (payload.error) {
        message = payload.error;
      }
    } catch {
      // ignore JSON parsing issues
    }
    throw new Error(message);
  }

  return (await response.json()) as PortfolioHistoryPayload;
}
