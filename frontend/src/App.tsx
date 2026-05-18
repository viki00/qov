import React, { useState, useEffect, useMemo } from 'react';
import axios from 'axios';
import { Calculator, Activity } from 'lucide-react';
import type { OptionChainResponse, OptionData, PnlData } from './types';

const API_BASE = ''; 

function App() {
  const [ticker, setTicker] = useState('AAPL');
  const [data, setData] = useState<OptionChainResponse | null>(null);
  const [loading, setLoading] = useState(false);
  
  // View State
  const [selectedExpiry, setSelectedExpiry] = useState<string>('');
  const [numStrikes, setNumStrikes] = useState<number>(10); 

  // P&L State
  const [selectedContract, setSelectedContract] = useState<{ticker: string, strike: number, type: string, data: OptionData} | null>(null);
  const [entryPrice, setEntryPrice] = useState<number>(0);
  const [quantity, setQuantity] = useState<number>(1);
  const [pnl, setPnl] = useState<PnlData | null>(null);

  const fetchOptions = async (t: string) => {
    setLoading(true);
    try {
      const res = await axios.get(`${API_BASE}/api/options/${t}`);
      const resData: OptionChainResponse = res.data;
      setData(resData);
      if (resData.expirations && resData.expirations.length > 0) {
        setSelectedExpiry(resData.expirations[0]);
      }
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchOptions(ticker);
  }, []);

  const handleSearch = (e: React.FormEvent) => {
    e.preventDefault();
    fetchOptions(ticker);
  };

  const calculatePnl = async () => {
    if (!selectedContract) return;
    try {
      const res = await axios.post(`${API_BASE}/api/pnl`, {
        entry_price: entryPrice,
        current_price: entryPrice * 1.1, 
        quantity: quantity,
        contract_type: selectedContract.type
      });
      setPnl(res.data);
    } catch (err) {
      console.error(err);
    }
  };

  const formatNum = (num: number | undefined, decimals = 2) => {
    if (num === undefined || num === null) return '-';
    return num.toLocaleString(undefined, { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
  };

  const filteredStrikes = useMemo(() => {
    if (!data || !selectedExpiry || !data.chain[selectedExpiry]) return [];
    
    const allStrikes = data.chain[selectedExpiry];
    if (numStrikes === 0) return allStrikes;

    const underlyingPrice = data.underlying?.price || 0;
    
    let atmIndex = 0;
    let minDiff = Infinity;
    for (let i = 0; i < allStrikes.length; i++) {
      const diff = Math.abs(allStrikes[i].strike - underlyingPrice);
      if (diff < minDiff) {
        minDiff = diff;
        atmIndex = i;
      }
    }

    const half = Math.floor(numStrikes / 2);
    const start = Math.max(0, atmIndex - half);
    const end = Math.min(allStrikes.length, atmIndex + half + (numStrikes % 2));
    
    return allStrikes.slice(start, end);
  }, [data, selectedExpiry, numStrikes]);

  const underlying = data?.underlying;
  const netChange = underlying ? underlying.price - underlying.prev_close : 0;
  const pctChange = underlying ? (netChange / underlying.prev_close) * 100 : 0;

  return (
    <div className="min-h-screen bg-[#020408] text-slate-300 font-sans selection:bg-blue-500/30">
      {/* Brand Header */}
      <header className="h-12 bg-[#0a0c10] border-b border-white/5 flex items-center justify-between px-4">
        <div className="flex items-center gap-2">
          <Activity className="w-5 h-5 text-blue-500" />
          <span className="text-white font-black tracking-tighter text-sm uppercase">QuantKube <span className="text-blue-500">QOV</span></span>
        </div>
        <form onSubmit={handleSearch} className="flex gap-2">
          <input 
            type="text" 
            value={ticker}
            onChange={(e) => setTicker(e.target.value.toUpperCase())}
            className="bg-white/5 border border-white/10 rounded px-3 py-1 text-xs focus:outline-none focus:ring-1 focus:ring-blue-500 w-24 text-white uppercase font-bold"
            placeholder="TICKER"
          />
          <button type="submit" className="bg-blue-600 hover:bg-blue-500 text-white text-[10px] font-bold px-3 rounded uppercase">Load</button>
        </form>
      </header>

      <main className="p-2 space-y-1">
        {/* CONSOLIDATED DATA & CONTROL ROW */}
        {underlying && (
          <div 
            style={{ 
              display: 'flex', 
              flexDirection: 'row', 
              flexWrap: 'nowrap', 
              alignItems: 'center', 
              whiteSpace: 'nowrap',
              background: '#0d0f14',
              border: '1px solid rgba(255,255,255,0.4)',
              fontSize: '11px',
              fontFamily: 'monospace',
              overflowX: 'auto'
            }}
            className="no-scrollbar shadow-lg"
          >
            {/* Ticker */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', gap: '2px' }}>
              <span style={{ color: '#64748b', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Ticker</span>
              <span style={{ color: 'white', fontWeight: '900' }}>{underlying.ticker}</span>
            </div>

            {/* % Change */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', fontWeight: 'bold', color: netChange >= 0 ? '#4ade80' : '#f87171' }}>
              {netChange >= 0 ? '▲' : '▼'} {formatNum(Math.abs(pctChange))}%
            </div>

            {/* Price */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', gap: '2px' }}>
              <span style={{ color: '#64748b', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Price</span>
              <span style={{ color: 'white' }}>{formatNum(underlying.price)}</span>
            </div>

            {/* Change */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', gap: '2px' }}>
              <span style={{ color: '#64748b', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Change</span>
              <span style={{ fontWeight: 'bold', color: netChange >= 0 ? '#4ade80' : '#f87171' }}>
                {netChange >= 0 ? '+' : ''}{formatNum(netChange)}
              </span>
            </div>

            {/* Bid */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', gap: '2px' }}>
              <span style={{ color: '#64748b', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Bid</span>
              <span style={{ color: '#cbd5e1' }}>{formatNum(underlying.bid)}</span>
            </div>

            {/* Ask */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', gap: '2px' }}>
              <span style={{ color: '#64748b', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Ask</span>
              <span style={{ color: '#cbd5e1' }}>{formatNum(underlying.ask)}</span>
            </div>

            {/* Volume */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', gap: '2px' }}>
              <span style={{ color: '#64748b', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Volume</span>
              <span style={{ color: '#94a3b8' }}>{underlying.volume.toLocaleString()}</span>
            </div>

            {/* High */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', gap: '2px' }}>
              <span style={{ color: '#64748b', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>High</span>
              <span style={{ color: 'rgba(74, 222, 128, 0.6)' }}>{formatNum(underlying.high)}</span>
            </div>

            {/* Low */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', gap: '2px' }}>
              <span style={{ color: '#64748b', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Low</span>
              <span style={{ color: 'rgba(248, 113, 113, 0.6)' }}>{formatNum(underlying.low)}</span>
            </div>

            {/* Current Date */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', color: '#94a3b8' }}>
              {new Date().toISOString().split('T')[0]}
            </div>

            {/* Expiry */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', background: 'rgba(59, 130, 246, 0.05)', color: '#60a5fa', fontWeight: '900' }}>
              {selectedExpiry || 'SELECT EXPIRY'}
            </div>

            {/* Strikes */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', gap: '8px' }}>
              <span style={{ color: '#64748b', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Strikes</span>
              <div style={{ display: 'flex', gap: '4px' }}>
                {[6, 10, 20, 0].map(val => (
                  <button
                    key={val}
                    onClick={() => setNumStrikes(val)}
                    className={`px-2 py-0.5 rounded text-[10px] font-bold border transition-all ${numStrikes === val ? 'bg-blue-600 border-blue-500 text-white' : 'bg-white/5 border-white/10 text-slate-500'}`}
                  >
                    {val === 0 ? 'ALL' : val}
                  </button>
                ))}
              </div>
            </div>

            {/* Call Label */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', borderRight: '1px solid rgba(255,255,255,0.2)', background: 'rgba(30, 58, 138, 0.1)', color: '#60a5fa', fontWeight: 'bold', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
              Call Contracts
            </div>
            
            {/* Put Label */}
            <div style={{ display: 'inline-flex', alignItems: 'center', padding: '4px 10px', background: 'rgba(88, 28, 135, 0.1)', color: '#c084fc', fontWeight: 'bold', fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.1em' }}>
              Put Contracts
            </div>

            {loading && (
              <div style={{ marginLeft: 'auto', display: 'inline-flex', alignItems: 'center', padding: '4px 10px', gap: '8px' }}>
                <div className="w-2 h-2 bg-blue-500 rounded-full animate-pulse"></div>
                <span style={{ color: '#64748b', fontWeight: 'bold', fontSize: '9px', textTransform: 'uppercase' }}>Syncing</span>
              </div>
            )}
          </div>
        )}

        {/* OPTION CHAIN GRID */}
        <div className="grid grid-cols-12 gap-1">
          <div className="col-span-12 xl:col-span-10">
            <div className="bg-[#080a0e] shadow-2xl overflow-hidden" style={{ border: '1px solid rgba(255,255,255,0.4)' }}>
              <div className="overflow-x-auto">
                <table className="w-full text-center border-collapse" style={{ borderCollapse: 'collapse', width: '100%' }}>
                  <thead>
                    <tr className="text-[10px] text-slate-500 font-bold uppercase bg-[#12161b]">
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-16">Delta</th>
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-16">Theta</th>
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-16 text-blue-400">Ask</th>
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-16 text-blue-400">Bid</th>
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-16">Change</th>
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-20">Last</th>
                      
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-3 py-2 bg-blue-600 text-white w-24">Strike</th>
                      
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-20">Last</th>
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-16">Change</th>
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-16 text-purple-400">Bid</th>
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-16 text-purple-400">Ask</th>
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-16">Theta</th>
                      <th style={{ border: '1px solid rgba(255,255,255,0.4)' }} className="px-1 py-2 w-16">Delta</th>
                    </tr>
                  </thead>
                  <tbody className="font-mono text-[11px]">
                    {filteredStrikes.map((s, i) => {
                      const isITM_Call = underlying ? s.strike < underlying.price : false;
                      const isITM_Put = underlying ? s.strike > underlying.price : false;
                      const isATM = underlying ? Math.abs(s.strike - underlying.price) < 1.5 : false;
                      
                      const cellStyle = { border: '1px solid rgba(255,255,255,0.4)' };
                      
                      return (
                        <tr key={i} className={`hover:bg-white/[0.03] transition-colors ${isATM ? 'bg-blue-500/[0.03]' : ''}`}>
                          {/* Call Side */}
                          <td style={cellStyle} className={`px-1 py-1.5 tabular-nums ${isITM_Call ? 'bg-blue-500/10 text-blue-200/80' : 'text-slate-500'}`}>{formatNum(s.call?.delta)}</td>
                          <td style={cellStyle} className={`px-1 py-1.5 tabular-nums text-red-500/40 ${isITM_Call ? 'bg-blue-500/10' : ''}`}>{formatNum(s.call?.theta)}</td>
                          <td style={cellStyle} className={`px-1 py-1.5 tabular-nums text-blue-400/90 ${isITM_Call ? 'bg-blue-500/15 font-bold' : ''}`}>{formatNum(s.call?.ask)}</td>
                          <td style={cellStyle} className={`px-1 py-1.5 tabular-nums text-blue-400/90 ${isITM_Call ? 'bg-blue-500/15 font-bold' : ''}`}>{formatNum(s.call?.bid)}</td>
                          <td style={cellStyle} className={`px-1 py-1.5 tabular-nums ${isITM_Call ? 'bg-blue-500/10' : ''} ${(s.call?.change || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>{formatNum(s.call?.change)}</td>
                          <td style={cellStyle} className={`px-1 py-1.5 text-xs font-black tabular-nums cursor-pointer hover:bg-blue-600/30 transition-all ${isITM_Call ? 'bg-blue-500/20 text-white' : 'text-slate-300'}`}
                              onClick={() => s.call && setSelectedContract({ticker: underlying?.ticker || '', strike: s.strike, type: 'call', data: s.call})}>
                            {formatNum(s.call?.last)}
                          </td>

                          {/* Strike */}
                          <td style={cellStyle} className={`px-3 py-1.5 text-xs font-black text-white tabular-nums bg-[#161b22] shadow-inner ${isATM ? 'ring-1 ring-blue-500/50 inset-shadow-blue-500/20' : ''}`}>
                            {s.strike.toFixed(1)}
                          </td>

                          {/* Put Side */}
                          <td style={cellStyle} className={`px-1 py-1.5 text-xs font-black tabular-nums cursor-pointer hover:bg-purple-600/30 transition-all ${isITM_Put ? 'bg-purple-500/20 text-white' : 'text-slate-300'}`}
                              onClick={() => s.put && setSelectedContract({ticker: underlying?.ticker || '', strike: s.strike, type: 'put', data: s.put})}>
                            {formatNum(s.put?.last)}
                          </td>
                          <td style={cellStyle} className={`px-1 py-1.5 tabular-nums ${isITM_Put ? 'bg-purple-500/10' : ''} ${(s.put?.change || 0) >= 0 ? 'text-green-400' : 'text-red-400'}`}>{formatNum(s.put?.change)}</td>
                          <td style={cellStyle} className={`px-1 py-1.5 tabular-nums text-purple-400/90 ${isITM_Put ? 'bg-purple-500/15 font-bold' : ''}`}>{formatNum(s.put?.bid)}</td>
                          <td style={cellStyle} className={`px-1 py-1.5 tabular-nums text-purple-400/90 ${isITM_Put ? 'bg-purple-500/15 font-bold' : ''}`}>{formatNum(s.put?.ask)}</td>
                          <td style={cellStyle} className={`px-1 py-1.5 tabular-nums text-red-500/40 ${isITM_Put ? 'bg-purple-500/10' : ''}`}>{formatNum(s.put?.theta)}</td>
                          <td style={cellStyle} className={`px-1 py-1.5 tabular-nums ${isITM_Put ? 'bg-purple-500/10 text-purple-200/80' : 'text-slate-500'}`}>{formatNum(s.put?.delta)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </div>

          <div className="col-span-12 xl:col-span-2">
            <div className="bg-[#0a0c10] border border-white/20 p-4 shadow-2xl h-full">
              <h2 className="font-bold text-[10px] mb-4 flex items-center gap-2 text-blue-500 uppercase tracking-widest">
                <Calculator className="w-3.5 h-3.5" /> Calculator
              </h2>
              
              {selectedContract ? (
                <div className="space-y-4">
                  <div className="bg-white/5 p-2 border border-white/20 rounded">
                    <p className="text-[9px] text-slate-500 font-bold uppercase tracking-tighter">Selected Target</p>
                    <p className="text-sm font-black text-white">{ticker} ${selectedContract.strike} {selectedContract.type.toUpperCase()}</p>
                  </div>

                  <div className="space-y-3">
                    <div>
                      <label className="block text-[9px] text-slate-500 uppercase font-black mb-1">Entry Price</label>
                      <input 
                        type="number" 
                        value={entryPrice}
                        onChange={(e) => setEntryPrice(parseFloat(e.target.value))}
                        className="w-full bg-[#020408] border border-white/20 rounded px-2 py-1.5 text-xs font-mono text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                      />
                    </div>
                    <div>
                      <label className="block text-[9px] text-slate-500 uppercase font-black mb-1">Qty</label>
                      <input 
                        type="number" 
                        value={quantity}
                        onChange={(e) => setQuantity(parseInt(e.target.value))}
                        className="w-full bg-[#020408] border border-white/20 rounded px-2 py-1.5 text-xs font-mono text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                      />
                    </div>
                  </div>

                  <button 
                    onClick={calculatePnl}
                    className="w-full bg-blue-600 hover:bg-blue-500 text-white font-black py-2 rounded text-[10px] uppercase tracking-widest transition-all shadow-lg shadow-blue-900/20"
                  >
                    Run Model
                  </button>

                  {pnl && (
                    <div className="mt-4 pt-4 border-t border-white/10 space-y-2">
                      <div className="flex justify-between items-center">
                        <span className="text-[9px] text-slate-500 font-bold uppercase">Net P&L</span>
                        <span className={`text-sm font-black tabular-nums ${pnl.net_income >= 0 ? 'text-green-400' : 'text-red-400'}`}>
                          ${pnl.net_income.toLocaleString()}
                        </span>
                      </div>
                      <div className="flex justify-between items-center text-[10px]">
                        <span className="text-slate-500">Yield</span>
                        <span className="text-white font-bold">{((pnl.net_income / pnl.entry_value) * 100).toFixed(2)}%</span>
                      </div>
                    </div>
                  )}
                </div>
              ) : (
                <div className="py-10 text-center border border-dashed border-white/20 rounded bg-white/[0.01]">
                  <p className="text-[9px] text-slate-600 px-4 font-bold uppercase leading-relaxed">Select contract to analyze risk</p>
                </div>
              )}
            </div>
          </div>
        </div>
      </main>
    </div>
  );
}

export default App;
