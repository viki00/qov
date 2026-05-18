export interface OptionData {
  last: number;
  change: number;
  bid: number;
  ask: number;
  volume: number;
  iv: number;
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  rho: number;
}

export interface StrikeGroup {
  strike: number;
  call: OptionData | null;
  put: OptionData | null;
}

export interface UnderlyingInfo {
  ticker: string;
  price: number;
  bid: number;
  ask: number;
  volume: number;
  open: number;
  high: number;
  low: number;
  prev_close: number;
}

export interface OptionChainResponse {
  underlying: UnderlyingInfo | null;
  expirations: string[];
  chain: Record<string, StrikeGroup[]>;
}

export interface PnlData {
  net_income: number;
  entry_value: number;
  current_value: number;
}
