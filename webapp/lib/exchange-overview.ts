import type { Account } from './types';

export type ExchangeOverviewRow = {
  exchange: string;
  balanceUsd: number;
  positionNotionalUsd: number;
  realLeverage: number | null;
};

export type ExchangeOverviewTotals = {
  label: string;
  balanceUsd: number;
  positionNotionalUsd: number;
  realLeverage: number | null;
};

export function buildExchangeOverviewRows(accounts: Account[]): ExchangeOverviewRow[] {
  return accounts.map((account) => ({
    exchange: account.exchange,
    balanceUsd: account.equity_usd,
    positionNotionalUsd: account.total_notional_usd,
    realLeverage: resolveRealLeverage(account.equity_usd, account.total_notional_usd),
  }));
}

export function buildExchangeOverviewTotals(accounts: Account[]): ExchangeOverviewTotals {
  const balanceUsd = accounts.reduce((total, account) => total + account.equity_usd, 0);
  const positionNotionalUsd = accounts.reduce((total, account) => total + account.total_notional_usd, 0);

  return {
    label: 'Total',
    balanceUsd,
    positionNotionalUsd,
    realLeverage: resolveRealLeverage(balanceUsd, positionNotionalUsd),
  };
}

function resolveRealLeverage(equityUsd: number, positionNotionalUsd: number): number | null {
  if (equityUsd > 0) {
    return positionNotionalUsd / equityUsd;
  }

  if (positionNotionalUsd > 0) {
    return null;
  }

  return 0;
}
