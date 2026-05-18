export type Position = {
  exchange: string;
  symbol: string;
  side: 'long' | 'short';
  size: number;
  entry_price: number;
  mark_price: number;
  leverage: number;
  liquidation_price: number | null;
  notional_usd: number;
  pnl_usd: number;
  delta_usd: number;
};

export type Account = {
  exchange: string;
  equity_usd: number;
  available_margin_usd: number;
  maintenance_margin_usd: number;
  position_count: number;
  total_notional_usd: number;
  total_pnl_usd: number;
  total_delta_usd: number;
  load_ratio: number;
  updated_at: string;
  positions: Position[];
};

export type ConnectorStatus = {
  exchange: string;
  ok: boolean;
  error: string | null;
  updated_at: string;
};

export type Risk = {
  net_delta_usd: number;
  margin_ratio: number;
  min_liq_distance_pct: number | null;
  risk_level: 'low' | 'medium' | 'high' | 'critical';
  warnings: string[];
  generated_at: string;
};

export type PortfolioHistorySnapshot = {
  recorded_at: string;
  total_equity_usd: number;
  total_available_margin_usd: number;
  total_maintenance_margin_usd: number;
  warning_count: number;
  warnings: string[];
};

export type HistoryChartPoint = {
  label: string;
  equity_usd: number;
  recorded_at: string;
};

export type PortfolioHistoryDay = {
  date: string;
  equity_usd: number;
  change_usd: number | null;
  warning_count: number;
  warnings: string[];
};

export type PortfolioHistoryPayload = {
  snapshots: PortfolioHistorySnapshot[];
  chart: HistoryChartPoint[];
  daily_changes: PortfolioHistoryDay[];
};

export type StatusPayload = {
  total_equity_usd: number;
  total_available_margin_usd: number;
  total_maintenance_margin_usd: number;
  accounts: Account[];
  connector_statuses: ConnectorStatus[];
  risk: Risk;
  current_snapshot: PortfolioHistorySnapshot;
  source?: string;
};
