export interface TickerInfo {
  symbol: string;
  name: string;
  price: number;
  change: number;
  changePct: number;
  bid: number;
  ask: number;
  volume: string;
  open: number;
  high: number;
  low: number;
  week52High: number;
  week52Low: number;
  ivRank: number;
  ivRv: number;
  hv30: number;
  iv: number;
  pe: number;
  marketCap: string;
  nextEarnings: string | null;
}

export interface ChainRow {
  strike: number;
  callBid: number;
  callAsk: number;
  callVolume: number;
  callOI: number;
  callDelta: number;
  callTheta: number;
  callGamma: number;
  callVega: number;
  callIV: number;
  putBid: number;
  putAsk: number;
  putVolume: number;
  putOI: number;
  putDelta: number;
  putTheta: number;
  putGamma: number;
  putVega: number;
  putIV: number;
  isATM: boolean;
  isCallITM: boolean;
}

export interface Account {
  id: number;
  startingBalance: number;
  cashBalance: number;
  createdAt: string;
}

export interface Position {
  id: string;
  symbol: string;
  strike: number;
  expiry: string;
  type: string;
  direction: string;
  qty: number;
  avgCost: number;
  last: number;
  marketValue: number;
  pnl: number;
  pnlPct: number;
  delta: number;
  theta: number;
  gamma: number;
}

export interface PortfolioGreeks {
  netDelta: number;
  netGamma: number;
  netTheta: number;
  netVega: number;
  totalPnL: number;
  marginUsed: number;
  cashBalance: number;
}

export interface DailyPnL {
  date: string;
  deltaPnl: number;
  thetaPnl: number;
  vegaPnl: number;
  residual: number;
  total: number;
}

export interface OrderTicket {
  symbol: string;
  strike: number;
  expiry: string;
  type: 'call' | 'put';
  direction: 'buy' | 'sell';
  price: number;
}
