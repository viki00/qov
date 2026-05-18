import React, { useState, useEffect, useMemo, useCallback } from 'react';
import axios from 'axios';
import { Activity } from 'lucide-react';
import type { TickerInfo, ChainRow, Account, Position, PortfolioGreeks, DailyPnL, OrderTicket } from './types';

const API = '';

// ── Signal helpers ────────────────────────────────────────────────────────────

function deltaSignal(nd: number): { color: string; badge: string } {
  const a = Math.abs(nd);
  if (a < 0.10) return { color: '#4ade80', badge: 'NEUTRAL' };
  if (a < 0.25) return { color: '#fbbf24', badge: 'HEDGE SOON' };
  return { color: '#f87171', badge: 'HEDGE NOW ⚠' };
}

function gammaSignal(ng: number, nd: number): { color: string; badge: string } {
  if (ng < -0.05)                        return { color: '#f87171', badge: 'ΓBLEED ⚡' };
  if (ng > 0.05 && Math.abs(nd) > 0.15) return { color: '#4ade80', badge: 'ΓHARVEST ↯' };
  if (ng < -0.02)                        return { color: '#fb923c', badge: 'ΓWATCH' };
  return { color: '#64748b', badge: 'ΓSTABLE' };
}

function fmt(n: number | undefined, d = 2): string {
  if (n === undefined || n === null || isNaN(n)) return '-';
  return n.toLocaleString(undefined, { minimumFractionDigits: d, maximumFractionDigits: d });
}

function fmtPnl(n: number): string {
  const abs = Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return (n >= 0 ? '+$' : '-$') + abs;
}

// ── Presentational atoms ──────────────────────────────────────────────────────

function DataCell({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.15)', gap: '4px' }}>
      <span style={{ color: '#475569', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>{label}</span>
      <span style={{ color: color || '#cbd5e1' }}>{value}</span>
    </div>
  );
}

function PortCell({ label, value, color, badge, badgeColor }: { label: string; value: string; color?: string; badge?: string; badgeColor?: string }) {
  return (
    <div style={{ display: 'inline-flex', alignItems: 'center', padding: '5px 12px', borderRight: '1px solid rgba(255,255,255,0.1)', gap: '6px' }}>
      <span style={{ color: '#475569', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>{label}</span>
      <span style={{ color: color || '#94a3b8', fontWeight: '700', fontSize: '12px', fontFamily: 'monospace' }}>{value}</span>
      {badge && (
        <span style={{ fontSize: '8px', fontWeight: '900', color: badgeColor || '#64748b', background: 'rgba(255,255,255,0.05)', padding: '1px 5px', borderRadius: '2px', textTransform: 'uppercase', letterSpacing: '0.05em' }}>
          {badge}
        </span>
      )}
    </div>
  );
}

// ── App ───────────────────────────────────────────────────────────────────────

export default function App() {
  const [inputTicker, setInputTicker]   = useState('AAPL');
  const [ticker, setTicker]             = useState('AAPL');
  const [tickerInfo, setTickerInfo]     = useState<TickerInfo | null>(null);
  const [expiries, setExpiries]         = useState<string[]>([]);
  const [selectedExpiry, setSelectedExpiry] = useState('');
  const [chainRows, setChainRows]       = useState<ChainRow[]>([]);
  const [numStrikes, setNumStrikes]     = useState(10);
  const [loading, setLoading]           = useState(false);

  const [account, setAccount]           = useState<Account | null>(null);
  const [positions, setPositions]       = useState<Position[]>([]);
  const [portfolio, setPortfolio]       = useState<PortfolioGreeks | null>(null);
  const [dailyPnl, setDailyPnl]         = useState<DailyPnL[]>([]);
  const [showHistory, setShowHistory]   = useState(false);

  const [orderTicket, setOrderTicket]   = useState<OrderTicket | null>(null);
  const [orderQty, setOrderQty]         = useState(1);
  const [rightTab, setRightTab]         = useState<'order' | 'positions'>('positions');
  const [orderStatus, setOrderStatus]   = useState<string | null>(null);
  const [snapMsg, setSnapMsg]           = useState<string | null>(null);

  // ── fetch helpers ──────────────────────────────────────────────────────────

  const loadChain = useCallback(async (sym: string, expiry: string) => {
    if (!expiry) return;
    try {
      const res = await axios.get(`${API}/api/chain/${sym}?expiry=${expiry}`);
      setChainRows(res.data);
    } catch (e) { console.error('chain', e); }
  }, []);

  const loadTicker = useCallback(async (sym: string) => {
    setLoading(true);
    try {
      const [ti, ex] = await Promise.all([
        axios.get(`${API}/api/ticker/${sym}`),
        axios.get(`${API}/api/expiries/${sym}`),
      ]);
      setTickerInfo(ti.data);
      const exps: string[] = ex.data;
      setExpiries(exps);
      if (exps.length > 0) {
        setSelectedExpiry(exps[0]);
        await loadChain(sym, exps[0]);
      }
    } catch (e) { console.error('ticker', e); }
    finally { setLoading(false); }
  }, [loadChain]);

  const loadPortfolio = useCallback(async () => {
    try {
      const [pos, port, acc] = await Promise.all([
        axios.get(`${API}/api/positions`),
        axios.get(`${API}/api/portfolio/greeks`),
        axios.get(`${API}/api/account`),
      ]);
      setPositions(pos.data);
      setPortfolio(port.data);
      setAccount(acc.data);
    } catch (e) { console.error('portfolio', e); }
  }, []);

  const loadDailyPnl = useCallback(async () => {
    try {
      const res = await axios.get(`${API}/api/pnl/daily`);
      setDailyPnl(res.data);
    } catch (e) { console.error('pnl', e); }
  }, []);

  useEffect(() => { loadTicker(ticker); loadPortfolio(); }, []);
  useEffect(() => {
    const iv = setInterval(loadPortfolio, 30000);
    return () => clearInterval(iv);
  }, [loadPortfolio]);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    const sym = inputTicker.toUpperCase();
    setTicker(sym);
    loadTicker(sym);
  };

  const handleExpiryChange = (exp: string) => {
    setSelectedExpiry(exp);
    loadChain(ticker, exp);
  };

  // ── order flow ─────────────────────────────────────────────────────────────

  const openOrder = (strike: number, type: 'call' | 'put', direction: 'buy' | 'sell', price: number) => {
    setOrderTicket({ symbol: ticker, strike, expiry: selectedExpiry, type, direction, price });
    setOrderQty(1);
    setOrderStatus(null);
    setRightTab('order');
  };

  const submitOrder = async () => {
    if (!orderTicket) return;
    try {
      await axios.post(`${API}/api/orders`, {
        symbol: orderTicket.symbol, strike: orderTicket.strike,
        expiry: orderTicket.expiry, type: orderTicket.type,
        direction: orderTicket.direction, qty: orderQty,
        limitPrice: orderTicket.price,
      });
      setOrderStatus('filled');
      await loadPortfolio();
      setTimeout(() => { setOrderTicket(null); setRightTab('positions'); setOrderStatus(null); }, 1500);
    } catch (e: any) {
      setOrderStatus(e?.response?.data?.detail || 'Order failed');
    }
  };

  const closePosition = async (pid: string) => {
    try {
      await axios.post(`${API}/api/positions/${pid}/close`);
      await loadPortfolio();
    } catch (e) { console.error('close', e); }
  };

  const takeSnapshot = async () => {
    try {
      const res = await axios.post(`${API}/api/pnl/snapshot`);
      setSnapMsg(`Snapshot saved (${res.data.snapped} position${res.data.snapped !== 1 ? 's' : ''})`);
      setTimeout(() => setSnapMsg(null), 3000);
      if (showHistory) loadDailyPnl();
    } catch (e) { console.error('snapshot', e); }
  };

  const toggleHistory = () => {
    const next = !showHistory;
    setShowHistory(next);
    if (next) loadDailyPnl();
  };

  // ── filtered strikes ───────────────────────────────────────────────────────

  const filteredStrikes = useMemo(() => {
    if (!chainRows.length) return [];
    if (numStrikes === 0) return chainRows;
    const price = tickerInfo?.price || 0;
    let atmIdx = 0, minDiff = Infinity;
    chainRows.forEach((r, i) => { const d = Math.abs(r.strike - price); if (d < minDiff) { minDiff = d; atmIdx = i; } });
    const half  = Math.floor(numStrikes / 2);
    const start = Math.max(0, atmIdx - half);
    return chainRows.slice(start, Math.min(chainRows.length, start + numStrikes));
  }, [chainRows, numStrikes, tickerInfo?.price]);

  // ── signals ────────────────────────────────────────────────────────────────

  const dSig = portfolio ? deltaSignal(portfolio.netDelta) : null;
  const gSig = portfolio ? gammaSignal(portfolio.netGamma, portfolio.netDelta) : null;

  const cs = { border: '1px solid rgba(255,255,255,0.4)' };

  // ── render ─────────────────────────────────────────────────────────────────

  return (
    <div className="min-h-screen bg-[#020408] text-slate-300 font-sans selection:bg-blue-500/30">

      {/* Header */}
      <header className="h-12 bg-[#0a0c10] border-b border-white/5 flex items-center justify-between px-4">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-500" />
          <span className="text-white font-black tracking-tighter text-sm uppercase">
            QuantKube <span className="text-blue-500">QOV</span>
          </span>
        </div>
        <form onSubmit={handleSearch} className="flex gap-2">
          <input type="text" value={inputTicker} onChange={e => setInputTicker(e.target.value.toUpperCase())}
            className="bg-white/5 border border-white/10 rounded px-3 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 w-24 text-white uppercase font-bold"
            placeholder="TICKER" />
          <button type="submit" className="bg-blue-600 hover:bg-blue-500 text-white text-[10px] font-bold px-3 rounded uppercase">Load</button>
        </form>
      </header>

      <main className="p-2 space-y-1">

        {/* Ticker data bar */}
        {tickerInfo && (
          <div style={{ display: 'flex', flexDirection: 'row', flexWrap: 'nowrap', alignItems: 'center', whiteSpace: 'nowrap', background: '#0d0f14', border: '1px solid rgba(255,255,255,0.4)', fontSize: '11px', fontFamily: 'monospace', overflowX: 'auto' }} className="no-scrollbar shadow-lg">
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.15)', gap: '4px' }}>
              <span style={{ color: '#475569', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Ticker</span>
              <span style={{ color: 'white', fontWeight: '900' }}>{tickerInfo.symbol}</span>
            </div>
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.15)', fontWeight: 'bold', color: tickerInfo.change >= 0 ? '#4ade80' : '#f87171' }}>
              {tickerInfo.change >= 0 ? '▲' : '▼'} {fmt(Math.abs(tickerInfo.changePct))}%
            </div>
            <DataCell label="Price"  value={fmt(tickerInfo.price)} />
            <DataCell label="Change" value={(tickerInfo.change >= 0 ? '+' : '') + fmt(tickerInfo.change)} color={tickerInfo.change >= 0 ? '#4ade80' : '#f87171'} />
            <DataCell label="Bid"    value={fmt(tickerInfo.bid)} />
            <DataCell label="Ask"    value={fmt(tickerInfo.ask)} />
            <DataCell label="Vol"    value={tickerInfo.volume} color="#94a3b8" />
            <DataCell label="High"   value={fmt(tickerInfo.high)} color="rgba(74,222,128,0.6)" />
            <DataCell label="Low"    value={fmt(tickerInfo.low)}  color="rgba(248,113,113,0.6)" />
            <DataCell label="IV"     value={fmt(tickerInfo.iv) + '%'}    color="#c084fc" />
            <DataCell label="IVR"    value={fmt(tickerInfo.ivRank)}      color="#60a5fa" />
            <DataCell label="HV30"   value={fmt(tickerInfo.hv30) + '%'} />
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.15)', color: '#475569' }}>
              {new Date().toISOString().split('T')[0]}
            </div>
            {/* Expiry selector */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.15)', gap: '6px' }}>
              <span style={{ color: '#475569', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Expiry</span>
              <select value={selectedExpiry} onChange={e => handleExpiryChange(e.target.value)}
                className="bg-[#0a0c10] border border-white/20 rounded px-2 py-0.5 text-[10px] font-bold text-blue-400 focus:outline-none">
                {expiries.map(e => <option key={e} value={e}>{e}</option>)}
              </select>
            </div>
            {/* Strike filter */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.15)', gap: '6px' }}>
              <span style={{ color: '#475569', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Strikes</span>
              <div style={{ display: 'flex', gap: '3px' }}>
                {[6, 10, 20, 0].map(v => (
                  <button key={v} onClick={() => setNumStrikes(v)}
                    className={`px-2 py-0.5 rounded text-[10px] font-bold border transition-all ${numStrikes === v ? 'bg-blue-600 border-blue-500 text-white' : 'bg-white/5 border-white/10 text-slate-500'}`}>
                    {v === 0 ? 'ALL' : v}
                  </button>
                ))}
              </div>
            </div>
            {loading && (
              <div style={{ marginLeft: 'auto', padding: '4px 10px' }} className="flex items-center gap-2">
                <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse" />
                <span style={{ color: '#475569', fontSize: '9px', fontWeight: 'bold', textTransform: 'uppercase' }}>Loading</span>
              </div>
            )}
          </div>
        )}

        {/* Portfolio Greeks bar */}
        {portfolio && dSig && gSig && (
          <div style={{ display: 'flex', flexDirection: 'row', flexWrap: 'nowrap', alignItems: 'center', whiteSpace: 'nowrap', background: '#080a0e', border: '1px solid rgba(255,255,255,0.25)', fontSize: '11px', fontFamily: 'monospace', overflowX: 'auto' }} className="no-scrollbar">
            <PortCell label="Δ Net Delta"    value={fmt(portfolio.netDelta, 3)} color={dSig.color} badge={dSig.badge} badgeColor={dSig.color} />
            <PortCell label="Γ Net Gamma"    value={fmt(portfolio.netGamma, 4)} color={gSig.color} badge={gSig.badge} badgeColor={gSig.color} />
            <PortCell label="Θ Daily Decay"  value={fmtPnl(portfolio.netTheta * 100)} color={portfolio.netTheta >= 0 ? '#4ade80' : '#f87171'} />
            <PortCell label="ν Net Vega"     value={fmt(portfolio.netVega, 3)} color="#a78bfa" />
            <PortCell label="Unrealized P&L" value={fmtPnl(portfolio.totalPnL)} color={portfolio.totalPnL >= 0 ? '#4ade80' : '#f87171'} />
            <PortCell label="Cash"           value={'$' + portfolio.cashBalance.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })} color="#94a3b8" />
            {account && (
              <PortCell label="Start" value={'$' + account.startingBalance.toLocaleString()} color="#475569" />
            )}
            <div style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', gap: '6px', padding: '4px 10px' }}>
              {snapMsg && <span style={{ color: '#4ade80', fontSize: '9px', fontWeight: 'bold' }}>{snapMsg}</span>}
              <button onClick={takeSnapshot}
                className="px-2 py-1 bg-white/5 border border-white/10 rounded text-[9px] font-bold text-slate-400 hover:bg-white/10 uppercase tracking-wider">
                Snapshot
              </button>
              <button onClick={toggleHistory}
                className={`px-2 py-1 border rounded text-[9px] font-bold uppercase tracking-wider transition-all ${showHistory ? 'bg-blue-600/20 border-blue-500/40 text-blue-400' : 'bg-white/5 border-white/10 text-slate-400 hover:bg-white/10'}`}>
                P&L History
              </button>
            </div>
          </div>
        )}

        {/* P&L History table (collapsible) */}
        {showHistory && (
          <div className="bg-[#080a0e] border border-white/15 p-3" style={{ fontFamily: 'monospace', fontSize: '11px' }}>
            <div className="text-[10px] font-bold text-slate-400 uppercase mb-2 tracking-wider">
              Daily P&L Attribution — delta · theta · vega · residual (gamma + slippage)
            </div>
            {dailyPnl.length === 0 ? (
              <p className="text-slate-600 text-[10px] py-4">
                No data yet. Click <strong className="text-slate-400">Snapshot</strong> today and again tomorrow — each pair of snapshots generates one row of attribution.
              </p>
            ) : (
              <table className="w-full border-collapse text-right" style={{ fontSize: '11px' }}>
                <thead>
                  <tr className="text-[9px] text-slate-500 uppercase border-b border-white/10">
                    <th className="text-left pr-4 py-1.5 font-bold">Date</th>
                    <th className="pr-4 py-1.5 font-bold text-blue-400">Δ Delta</th>
                    <th className="pr-4 py-1.5 font-bold text-red-400">Θ Theta</th>
                    <th className="pr-4 py-1.5 font-bold text-purple-400">ν Vega</th>
                    <th className="pr-4 py-1.5 font-bold text-slate-400">Γ Residual</th>
                    <th className="py-1.5 font-black text-white">Total</th>
                  </tr>
                </thead>
                <tbody>
                  {dailyPnl.map(row => (
                    <tr key={row.date} className="border-t border-white/5 hover:bg-white/[0.02]">
                      <td className="text-left pr-4 py-1.5 text-slate-400">{row.date}</td>
                      <td className={`pr-4 py-1.5 tabular-nums ${row.deltaPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtPnl(row.deltaPnl)}</td>
                      <td className={`pr-4 py-1.5 tabular-nums ${row.thetaPnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtPnl(row.thetaPnl)}</td>
                      <td className={`pr-4 py-1.5 tabular-nums ${row.vegaPnl  >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtPnl(row.vegaPnl)}</td>
                      <td className={`pr-4 py-1.5 tabular-nums ${row.residual >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtPnl(row.residual)}</td>
                      <td className={`py-1.5 tabular-nums font-black text-sm ${row.total >= 0 ? 'text-green-400' : 'text-red-400'}`}>{fmtPnl(row.total)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        )}

        {/* Main grid: chain + right panel */}
        <div className="grid grid-cols-12 gap-1">

          {/* Chain table */}
          <div className="col-span-9">
            <div className="bg-[#080a0e] shadow-2xl overflow-hidden" style={{ border: '1px solid rgba(255,255,255,0.4)' }}>
              <div className="overflow-x-auto">
                <table className="w-full text-center" style={{ borderCollapse: 'collapse' }}>
                  <thead>
                    <tr className="text-[10px] text-slate-500 font-bold uppercase bg-[#12161b]">
                      <th style={cs} className="px-1 py-2 w-12">Δ</th>
                      <th style={cs} className="px-1 py-2 w-12">Θ</th>
                      <th style={cs} className="px-1 py-2 w-16 text-green-400/70">Ask↑</th>
                      <th style={cs} className="px-1 py-2 w-16 text-red-400/70">Bid↓</th>
                      <th style={cs} className="px-1 py-2 w-10">Vol</th>
                      <th style={cs} className="px-3 py-2 bg-blue-600 text-white w-24">Strike</th>
                      <th style={cs} className="px-1 py-2 w-10">Vol</th>
                      <th style={cs} className="px-1 py-2 w-16 text-red-400/70">Bid↓</th>
                      <th style={cs} className="px-1 py-2 w-16 text-green-400/70">Ask↑</th>
                      <th style={cs} className="px-1 py-2 w-12">Θ</th>
                      <th style={cs} className="px-1 py-2 w-12">Δ</th>
                    </tr>
                    <tr className="text-[9px] font-bold bg-[#0d1117]">
                      <th style={{ ...cs, color: 'rgba(96,165,250,0.4)' }} colSpan={4} className="py-0.5">◀ CALLS</th>
                      <th style={cs} className="py-0.5" />
                      <th style={cs} className="py-0.5 bg-blue-900/10" />
                      <th style={cs} className="py-0.5" />
                      <th style={{ ...cs, color: 'rgba(192,132,252,0.4)' }} colSpan={4} className="py-0.5">PUTS ▶</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono text-[11px]">
                    {filteredStrikes.map((row, i) => {
                      const price    = tickerInfo?.price || 0;
                      const itmCall  = row.strike < price;
                      const itmPut   = row.strike > price;
                      const isATM    = Math.abs(row.strike - price) < 1.5;
                      return (
                        <tr key={i} className={`hover:bg-white/[0.03] transition-colors ${isATM ? 'bg-blue-500/[0.02]' : ''}`}>
                          {/* Call Δ */}
                          <td style={cs} className={`px-1 py-1.5 tabular-nums ${itmCall ? 'bg-blue-500/10 text-blue-200/80' : 'text-slate-500'}`}>{fmt(row.callDelta)}</td>
                          {/* Call Θ */}
                          <td style={cs} className={`px-1 py-1.5 tabular-nums text-red-500/40 ${itmCall ? 'bg-blue-500/10' : ''}`}>{fmt(row.callTheta)}</td>
                          {/* Call Ask → BUY */}
                          <td style={cs} title={`BUY ${ticker} $${row.strike}C @ ${row.callAsk}`}
                            className={`px-1 py-1.5 tabular-nums font-bold cursor-pointer text-blue-400 hover:bg-green-600/25 hover:text-green-300 transition-all ${itmCall ? 'bg-blue-500/15' : ''}`}
                            onClick={() => openOrder(row.strike, 'call', 'buy', row.callAsk)}>
                            {fmt(row.callAsk)}
                          </td>
                          {/* Call Bid → SELL */}
                          <td style={cs} title={`SELL ${ticker} $${row.strike}C @ ${row.callBid}`}
                            className={`px-1 py-1.5 tabular-nums cursor-pointer text-blue-400/60 hover:bg-red-600/25 hover:text-red-300 transition-all ${itmCall ? 'bg-blue-500/10' : ''}`}
                            onClick={() => openOrder(row.strike, 'call', 'sell', row.callBid)}>
                            {fmt(row.callBid)}
                          </td>
                          {/* Call Vol */}
                          <td style={cs} className={`px-1 py-1.5 tabular-nums text-slate-600 text-[10px] ${itmCall ? 'bg-blue-500/5' : ''}`}>{row.callVolume > 0 ? row.callVolume.toLocaleString() : '-'}</td>
                          {/* Strike */}
                          <td style={cs} className={`px-3 py-1.5 text-xs font-black text-white tabular-nums bg-[#161b22] shadow-inner ${isATM ? 'ring-1 ring-blue-500/40' : ''}`}>
                            {row.strike.toFixed(1)}
                          </td>
                          {/* Put Vol */}
                          <td style={cs} className={`px-1 py-1.5 tabular-nums text-slate-600 text-[10px] ${itmPut ? 'bg-purple-500/5' : ''}`}>{row.putVolume > 0 ? row.putVolume.toLocaleString() : '-'}</td>
                          {/* Put Bid → SELL */}
                          <td style={cs} title={`SELL ${ticker} $${row.strike}P @ ${row.putBid}`}
                            className={`px-1 py-1.5 tabular-nums cursor-pointer text-purple-400/60 hover:bg-red-600/25 hover:text-red-300 transition-all ${itmPut ? 'bg-purple-500/10' : ''}`}
                            onClick={() => openOrder(row.strike, 'put', 'sell', row.putBid)}>
                            {fmt(row.putBid)}
                          </td>
                          {/* Put Ask → BUY */}
                          <td style={cs} title={`BUY ${ticker} $${row.strike}P @ ${row.putAsk}`}
                            className={`px-1 py-1.5 tabular-nums font-bold cursor-pointer text-purple-400 hover:bg-green-600/25 hover:text-green-300 transition-all ${itmPut ? 'bg-purple-500/15' : ''}`}
                            onClick={() => openOrder(row.strike, 'put', 'buy', row.putAsk)}>
                            {fmt(row.putAsk)}
                          </td>
                          {/* Put Θ */}
                          <td style={cs} className={`px-1 py-1.5 tabular-nums text-red-500/40 ${itmPut ? 'bg-purple-500/10' : ''}`}>{fmt(row.putTheta)}</td>
                          {/* Put Δ */}
                          <td style={cs} className={`px-1 py-1.5 tabular-nums ${itmPut ? 'bg-purple-500/10 text-purple-200/80' : 'text-slate-500'}`}>{fmt(row.putDelta)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
            <p className="text-[9px] text-slate-700 mt-0.5 font-mono px-1">Ask↑ = click to buy · Bid↓ = click to sell · hover to confirm side</p>
          </div>

          {/* Right panel */}
          <div className="col-span-3">
            <div className="bg-[#0a0c10] border border-white/20 shadow-2xl flex flex-col" style={{ minHeight: '400px' }}>

              {/* Tabs */}
              <div className="flex border-b border-white/10 flex-shrink-0">
                {(['order', 'positions'] as const).map(tab => (
                  <button key={tab} onClick={() => setRightTab(tab)}
                    className={`flex-1 py-2 text-[10px] font-bold uppercase tracking-wider transition-all ${rightTab === tab ? 'text-blue-400 border-b-2 border-blue-500 bg-blue-500/5' : 'text-slate-600 hover:text-slate-400'}`}>
                    {tab === 'positions' && positions.length > 0
                      ? <>{tab} <span className="ml-1 bg-blue-600 text-white rounded-full px-1.5 py-0.5 text-[8px]">{positions.length}</span></>
                      : tab}
                  </button>
                ))}
              </div>

              {/* Order ticket */}
              {rightTab === 'order' && (
                <div className="p-3 space-y-3 font-mono text-[11px] flex-1">
                  {orderTicket ? (
                    <>
                      <div className="bg-white/5 border border-white/10 rounded p-2">
                        <p className="text-[9px] text-slate-500 uppercase font-bold mb-0.5">Contract</p>
                        <p className="text-white font-black text-sm">{orderTicket.symbol} ${orderTicket.strike} {orderTicket.type.toUpperCase()}</p>
                        <p className="text-slate-400 text-[10px]">{orderTicket.expiry}</p>
                      </div>

                      <div className="flex gap-2">
                        {(['buy', 'sell'] as const).map(dir => (
                          <button key={dir} onClick={() => setOrderTicket(t => t ? { ...t, direction: dir } : t)}
                            className={`flex-1 py-1.5 rounded text-[10px] font-black uppercase transition-all ${orderTicket.direction === dir ? (dir === 'buy' ? 'bg-green-600 text-white' : 'bg-red-600 text-white') : 'bg-white/5 text-slate-500 border border-white/10'}`}>
                            {dir}
                          </button>
                        ))}
                      </div>

                      <div>
                        <label className="text-[9px] text-slate-500 uppercase font-black block mb-1">Limit Price</label>
                        <input type="number" step="0.01" value={orderTicket.price}
                          onChange={e => setOrderTicket(t => t ? { ...t, price: parseFloat(e.target.value) } : t)}
                          className="w-full bg-[#020408] border border-white/20 rounded px-2 py-1.5 text-xs font-mono text-white focus:outline-none focus:ring-1 focus:ring-blue-500" />
                      </div>

                      <div>
                        <label className="text-[9px] text-slate-500 uppercase font-black block mb-1">Qty (contracts)</label>
                        <input type="number" min={1} value={orderQty}
                          onChange={e => setOrderQty(Math.max(1, parseInt(e.target.value) || 1))}
                          className="w-full bg-[#020408] border border-white/20 rounded px-2 py-1.5 text-xs font-mono text-white focus:outline-none focus:ring-1 focus:ring-blue-500" />
                      </div>

                      <div className="bg-white/[0.02] border border-white/10 rounded p-2 space-y-1 text-[10px]">
                        <div className="flex justify-between">
                          <span className="text-slate-500">Total Cost</span>
                          <span className="text-white font-black">${(orderTicket.price * orderQty * 100).toLocaleString(undefined, { minimumFractionDigits: 2 })}</span>
                        </div>
                        {account && (() => {
                          const cash = account.cashBalance;
                          const cost = orderTicket.price * orderQty * 100;
                          const after = orderTicket.direction === 'buy' ? cash - cost : cash + cost;
                          return (
                            <div className="flex justify-between">
                              <span className="text-slate-500">Cash After</span>
                              <span className={`font-bold ${after >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                                ${after.toLocaleString(undefined, { minimumFractionDigits: 2 })}
                              </span>
                            </div>
                          );
                        })()}
                      </div>

                      {orderStatus === 'filled' ? (
                        <div className="py-2 text-center text-green-400 font-black text-xs uppercase tracking-widest">✓ Filled</div>
                      ) : orderStatus ? (
                        <div className="py-2 text-center text-red-400 text-xs">{orderStatus}</div>
                      ) : (
                        <button onClick={submitOrder}
                          className={`w-full py-2 rounded text-[10px] font-black uppercase tracking-widest transition-all ${orderTicket.direction === 'buy' ? 'bg-green-600 hover:bg-green-500 shadow-green-900/20' : 'bg-red-600 hover:bg-red-500 shadow-red-900/20'} text-white shadow-lg`}>
                          Confirm {orderTicket.direction.toUpperCase()}
                        </button>
                      )}

                      <button onClick={() => setOrderTicket(null)}
                        className="w-full py-1 text-[9px] text-slate-600 hover:text-slate-400 uppercase">
                        Cancel
                      </button>
                    </>
                  ) : (
                    <div className="py-12 text-center border border-dashed border-white/10 rounded bg-white/[0.01] mt-2">
                      <p className="text-[9px] text-slate-600 font-bold uppercase leading-relaxed px-4">
                        Click <span className="text-green-400">Ask</span> to buy<br />
                        Click <span className="text-red-400">Bid</span> to sell
                      </p>
                    </div>
                  )}
                </div>
              )}

              {/* Positions panel */}
              {rightTab === 'positions' && (
                <div className="p-2 font-mono text-[11px] flex-1 overflow-y-auto">
                  {positions.length === 0 ? (
                    <div className="py-12 text-center border border-dashed border-white/10 rounded bg-white/[0.01] m-2">
                      <p className="text-[9px] text-slate-600 font-bold uppercase">No open positions</p>
                    </div>
                  ) : (
                    <div className="space-y-1.5">
                      {portfolio && (
                        <div className="flex justify-between items-center py-1.5 px-2 bg-white/5 rounded border border-white/10 mb-2">
                          <span className="text-[9px] text-slate-500 uppercase font-bold">Total Unrealized</span>
                          <span className={`font-black text-sm ${portfolio.totalPnL >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                            {fmtPnl(portfolio.totalPnL)}
                          </span>
                        </div>
                      )}
                      {positions.map(pos => (
                        <div key={pos.id} className="border border-white/10 rounded p-2 bg-white/[0.015] hover:bg-white/[0.03] transition-all">
                          <div className="flex justify-between items-start mb-1">
                            <div className="flex items-center gap-1.5 flex-wrap">
                              <span className="text-white font-black">{pos.symbol}</span>
                              <span className="text-slate-400">${pos.strike} {pos.type.toUpperCase()}</span>
                              <span className={`text-[8px] font-black px-1.5 py-0.5 rounded ${pos.direction === 'buy' ? 'bg-green-900/30 text-green-400' : 'bg-red-900/30 text-red-400'}`}>
                                {pos.direction.toUpperCase()}
                              </span>
                            </div>
                            <button onClick={() => closePosition(pos.id)}
                              className="text-[8px] text-slate-600 hover:text-red-400 uppercase font-bold border border-white/10 px-1.5 py-0.5 rounded hover:border-red-500/30 transition-all flex-shrink-0 ml-1">
                              Close
                            </button>
                          </div>
                          <div className="text-[9px] text-slate-600 mb-1">{pos.expiry} · {pos.qty}x @ ${pos.avgCost.toFixed(2)}</div>
                          <div className="flex justify-between items-center">
                            <div className="text-[9px] text-slate-500">
                              Last <span className="text-slate-300">{fmt(pos.last)}</span>
                              <span className="ml-2">Δ{fmt(pos.delta, 3)}</span>
                              <span className="ml-1">Θ{fmt(pos.theta, 3)}</span>
                            </div>
                            <div className={`font-black text-xs ${pos.pnl >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                              {fmtPnl(pos.pnl)}
                              <span className="ml-1 text-[8px] font-normal opacity-60">({pos.pnlPct >= 0 ? '+' : ''}{fmt(pos.pnlPct)}%)</span>
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>

        </div>
      </main>
    </div>
  );
}
