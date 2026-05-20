export const DEFAULT_API_BASE = 'http://127.0.0.1:8080';

export function normalizeApiBase(rawApiBase: string | undefined): string {
  const apiBase = rawApiBase?.trim() || DEFAULT_API_BASE;

  try {
    const url = new URL(apiBase);
    if (/^\d+\.\d+\.\d+\.\d+$/.test(url.hostname) && !['127.0.0.1', '0.0.0.0'].includes(url.hostname)) {
      url.hostname = `${url.hostname.replace(/\./g, '-')}.sslip.io`;
      return url.toString().replace(/\/$/, '');
    }
  } catch {
    return apiBase;
  }

  return apiBase;
}
