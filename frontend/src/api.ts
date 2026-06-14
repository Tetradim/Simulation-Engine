export type SimulationConfig = {
  starting_cash: number;
  default_quantity: number;
  max_allocation_pct: number;
  fill_ratio: number;
  slippage_bps: number;
  commission_per_order: number;
  latency_ms: number;
  reject_below_confidence: number;
  default_trailing_percent: number;
  regular_stop_percent: number;
  take_profit_percent: number;
  signal_buy_threshold: number;
  signal_sell_threshold: number;
};

export type Position = {
  symbol: string;
  quantity: number;
  avg_entry: number;
  entry_price: number;
  current_price: number;
  pnl: number;
  pnl_pct: number;
  trailing_enabled: boolean;
  trailing_percent?: number | null;
};

export type ReplaySession = {
  session_id: string;
  name: string;
  source: string;
  symbols: string[];
  bar_count: number;
  first_timestamp: string;
  last_timestamp: string;
};

export type SimulationSnapshot = {
  config: SimulationConfig;
  sessions: ReplaySession[];
  replay: {
    active: boolean;
    session_id?: string | null;
    speed: number;
    loop: boolean;
    index: number;
    current_timestamp?: string | null;
  };
  current_prices: Record<string, number>;
  account: {
    starting_cash: number;
    cash: number;
    total_equity: number;
    buying_power: number;
    day_pnl_dollar: number;
    day_pnl_pct: number;
    open_positions: number;
    positions: Record<string, Position>;
  };
  tickers: Array<{ symbol: string; enabled: boolean; trailing_enabled: boolean; trailing_percent?: number | null; auto_stop_reason?: string | null }>;
  decisions: Array<Record<string, unknown>>;
  event_log: Array<Record<string, unknown>>;
};

export async function requestJson<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(path, {
    ...options,
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      ...(options.headers || {}),
    },
  });
  const payload = (await response.json()) as unknown;
  if (!response.ok) {
    const message = payload && typeof payload === 'object' && 'detail' in payload ? String((payload as { detail: unknown }).detail) : `${response.status} ${response.statusText}`;
    throw new Error(message);
  }
  return payload as T;
}

export const api = {
  state: () => requestJson<SimulationSnapshot>('/api/simulation/state'),
  updateConfig: (config: SimulationConfig) =>
    requestJson<SimulationSnapshot>('/api/simulation/config', {
      method: 'PUT',
      body: JSON.stringify(config),
    }),
  importCsv: (name: string, csvText: string) =>
    requestJson<{ ok: boolean; session: ReplaySession }>('/api/simulation/replay/import/csv', {
      method: 'POST',
      body: JSON.stringify({ name, csv_text: csvText }),
    }),
  startReplay: (sessionId: string, speed: number, loop: boolean) =>
    requestJson<SimulationSnapshot>(`/api/simulation/replay/sessions/${sessionId}/start`, {
      method: 'POST',
      body: JSON.stringify({ speed, loop }),
    }),
  stepReplay: () => requestJson<SimulationSnapshot>('/api/simulation/replay/step', { method: 'POST', body: '{}' }),
  stopReplay: () => requestJson<SimulationSnapshot>('/api/simulation/replay/stop', { method: 'POST', body: '{}' }),
  handoff: (payload: Record<string, unknown>) =>
    requestJson<Record<string, unknown>>('/api/edge/handoff', {
      method: 'POST',
      headers: { 'X-API-Key': 'local-sim-key' },
      body: JSON.stringify(payload),
    }),
};
