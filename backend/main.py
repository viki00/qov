from fastapi import FastAPI, APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from elasticsearch import AsyncElasticsearch
import asyncio, json, os, time, uuid, warnings
from datetime import date, timedelta
import yfinance as yf
import numpy as np

warnings.filterwarnings("ignore", message=".*verify_certs.*")
warnings.filterwarnings("ignore", category=DeprecationWarning)

app = FastAPI(title="QOV API")
router = APIRouter(prefix="/api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_methods=["*"],
    allow_headers=["*"], allow_credentials=True,
)

ES_HOST = os.getenv("ES_HOST", "https://elasticsearch-master.pipeline.svc.cluster.local:9200")
ES_USER = os.getenv("ES_USER", "elastic")
ES_PASS = os.getenv("ES_PASSWORD", "")
INDEX   = "sp500-greeks-*"

es = AsyncElasticsearch(
    ES_HOST,
    basic_auth=(ES_USER, ES_PASS),
    verify_certs=False,
    ssl_show_warn=False,
)

WATCHLIST    = ["NVDA", "AAPL", "MSFT", "TSLA", "GOOGL", "AMZN", "SPY", "QQQ"]
positions_db: dict[str, dict] = {}
trade_log:    list[dict]      = []

# ── yfinance cache ──────────────────────────────────────────────────────────
_yf_cache: dict[str, tuple[dict, float]] = {}
YF_TTL = 300

def _fmt_vol(n: int) -> str:
    if n >= 1_000_000: return f"{n/1_000_000:.1f}M"
    if n >= 1_000:     return f"{n/1_000:.0f}K"
    return str(n)

def _fmt_cap(n: float) -> str:
    if n >= 1e12: return f"${n/1e12:.2f}T"
    if n >= 1e9:  return f"${n/1e9:.2f}B"
    if n >= 1e6:  return f"${n/1e6:.2f}M"
    return "N/A"

def _get_yf_blocking(sym: str) -> dict:
    now = time.time()
    if sym in _yf_cache and now - _yf_cache[sym][1] < YF_TTL:
        return _yf_cache[sym][0]
    try:
        tk   = yf.Ticker(sym)
        info = tk.info
        hv30 = 0.0
        try:
            hist = tk.history(period="35d")
            if len(hist) >= 5:
                rets = np.log(hist["Close"] / hist["Close"].shift(1)).dropna()
                hv30 = float(rets.std() * np.sqrt(252) * 100)
        except Exception:
            pass
        earn = info.get("earningsDate") or info.get("earningsTimestamp")
        if isinstance(earn, list):
            earn = earn[0] if earn else None
        if hasattr(earn, "strftime"):
            earn = earn.strftime("%b %d")
        elif isinstance(earn, (int, float)):
            from datetime import datetime
            earn = datetime.utcfromtimestamp(earn).strftime("%b %d")
        elif earn:
            earn = str(earn)[:10]
        else:
            earn = None
        d: dict = {
            "name":        info.get("shortName") or info.get("longName") or sym,
            "week52High":  float(info.get("fiftyTwoWeekHigh") or 0),
            "week52Low":   float(info.get("fiftyTwoWeekLow")  or 0),
            "pe":          round(float(info.get("trailingPE") or 0), 1),
            "marketCap":   _fmt_cap(float(info.get("marketCap") or 0)),
            "nextEarnings": earn,
            "hv30":        round(hv30, 1),
        }
    except Exception:
        d = {"name": sym, "week52High": 0.0, "week52Low": 0.0,
             "pe": 0.0, "marketCap": "N/A", "nextEarnings": None, "hv30": 0.0}
    _yf_cache[sym] = (d, time.time())
    return d

# ── ES helpers ───────────────────────────────────────────────────────────────

async def _latest_underlying(sym: str) -> dict | None:
    r = await es.search(
        index=INDEX,
        query={"term": {"ticker.keyword": sym}},
        size=1,
        sort=[{"timestamp": "desc"}],
        _source=["underlying_price", "underlying_bid", "underlying_ask",
                 "underlying_volume", "underlying_open", "underlying_high",
                 "underlying_low", "underlying_prev_close"],
    )
    hits = r["hits"]["hits"]
    return hits[0]["_source"] if hits else None

async def _expiry_list(sym: str) -> list[str]:
    today = date.today().isoformat()
    r = await es.search(
        index=INDEX,
        query={"term": {"ticker.keyword": sym}},
        aggs={"e": {"terms": {"field": "expiry", "size": 50}}},
        size=0,
    )
    dates = sorted(b["key_as_string"][:10] for b in r["aggregations"]["e"]["buckets"])
    return [d for d in dates if d >= today]

# ── REST endpoints ────────────────────────────────────────────────────────────

@router.get("/ticker/{sym}")
async def get_ticker(sym: str):
    sym = sym.upper()
    u = await _latest_underlying(sym)
    if not u:
        raise HTTPException(404, f"No data for {sym}")

    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, _get_yf_blocking, sym)

    price = float(u["underlying_price"])
    prev  = float(u.get("underlying_prev_close") or price)
    ch    = price - prev

    # ATM IV from the nearest expiry
    iv_pct = 0.0
    expiries = await _expiry_list(sym)
    if expiries:
        r2 = await es.search(
            index=INDEX,
            query={"bool": {"must": [
                {"term": {"ticker.keyword": sym}},
                {"term": {"expiry": expiries[0]}},
                {"term": {"type.keyword": "call"}},
            ]}},
            aggs={"strikes": {"terms": {"field": "strike", "size": 300}}},
            size=0,
        )
        buckets = r2["aggregations"]["strikes"]["buckets"]
        if buckets:
            atm_k = min(buckets, key=lambda b: abs(b["key"] - price))["key"]
            r3 = await es.search(
                index=INDEX,
                query={"bool": {"must": [
                    {"term": {"ticker.keyword": sym}},
                    {"term": {"expiry": expiries[0]}},
                    {"term": {"type.keyword": "call"}},
                    {"term": {"strike": atm_k}},
                ]}},
                size=1, sort=[{"timestamp": "desc"}], _source=["iv"],
            )
            if r3["hits"]["hits"]:
                iv_pct = round(float(r3["hits"]["hits"][0]["_source"].get("iv", 0)) * 100, 1)

    iv_rank_data = await _ticker_iv_rank(sym)
    iv_rank = iv_rank_data["ivRank"] if iv_rank_data else 0.0
    iv_rv   = iv_rank_data["ivRv"]   if iv_rank_data else 0.0

    return {
        "symbol":       sym,
        "name":         info["name"],
        "price":        price,
        "change":       round(ch, 2),
        "changePct":    round(ch / prev * 100, 2) if prev else 0.0,
        "bid":          float(u.get("underlying_bid",    price)),
        "ask":          float(u.get("underlying_ask",    price)),
        "volume":       _fmt_vol(int(u.get("underlying_volume", 0))),
        "open":         float(u.get("underlying_open",   0)),
        "high":         float(u.get("underlying_high",   0)),
        "low":          float(u.get("underlying_low",    0)),
        "week52High":   info["week52High"],
        "week52Low":    info["week52Low"],
        "ivRank":       iv_rank,
        "ivRv":         iv_rv,
        "hv30":         info["hv30"],
        "iv":           iv_pct,
        "pe":           info["pe"],
        "marketCap":    info["marketCap"],
        "nextEarnings": info["nextEarnings"],
    }

@router.get("/expiries/{sym}")
async def get_expiries(sym: str):
    e = await _expiry_list(sym.upper())
    if not e:
        raise HTTPException(404, f"No expiries for {sym}")
    return e

@router.get("/chain/{sym}")
async def get_chain(sym: str, expiry: str = Query(...)):
    sym = sym.upper()
    r = await es.search(
        index=INDEX,
        query={"bool": {"must": [
            {"term": {"ticker.keyword": sym}},
            {"term": {"expiry": expiry}},
        ]}},
        size=10000,
        sort=[{"timestamp": "desc"}],
    )
    hits = r["hits"]["hits"]
    if not hits:
        raise HTTPException(404, f"No chain for {sym} {expiry}")

    price = float(hits[0]["_source"].get("underlying_price", 0))

    by_strike: dict[float, dict] = {}
    seen: set[tuple] = set()
    for h in hits:
        s   = h["_source"]
        key = (float(s["strike"]), s["type"])
        if key in seen:
            continue
        seen.add(key)
        k = float(s["strike"])
        if k not in by_strike:
            by_strike[k] = {"call": None, "put": None}
        by_strike[k][s["type"]] = s

    all_strikes = sorted(by_strike)
    atm = min(all_strikes, key=lambda k: abs(k - price)) if all_strikes else None

    def v(o: dict | None, field: str, default: float = 0.0) -> float:
        return float((o or {}).get(field, default))

    return [
        {
            "strike":     k,
            "callBid":    v(by_strike[k]["call"], "bid"),
            "callAsk":    v(by_strike[k]["call"], "ask"),
            "callVolume": int(v(by_strike[k]["call"], "volume")),
            "callOI":     int(v(by_strike[k]["call"], "open_interest")),
            "callDelta":  round(v(by_strike[k]["call"], "delta"), 4),
            "callTheta":  round(v(by_strike[k]["call"], "theta"), 4),
            "callGamma":  round(v(by_strike[k]["call"], "gamma"), 6),
            "callVega":   round(v(by_strike[k]["call"], "vega"),  4),
            "callIV":     round(v(by_strike[k]["call"], "iv") * 100, 1),
            "putBid":     v(by_strike[k]["put"],  "bid"),
            "putAsk":     v(by_strike[k]["put"],  "ask"),
            "putVolume":  int(v(by_strike[k]["put"],  "volume")),
            "putOI":      int(v(by_strike[k]["put"],  "open_interest")),
            "putDelta":   round(v(by_strike[k]["put"],  "delta"), 4),
            "putTheta":   round(v(by_strike[k]["put"],  "theta"), 4),
            "putGamma":   round(v(by_strike[k]["put"],  "gamma"), 6),
            "putVega":    round(v(by_strike[k]["put"],  "vega"),  4),
            "putIV":      round(v(by_strike[k]["put"],  "iv") * 100, 1),
            "isATM":      k == atm,
            "isCallITM":  k < price,
        }
        for k in all_strikes
    ]

@router.get("/greeks/{sym}")
async def get_greeks(
    sym: str,
    strike: float,
    expiry: str,
    type_: str = Query(..., alias="type"),
):
    r = await es.search(
        index=INDEX,
        query={"bool": {"must": [
            {"term": {"ticker.keyword": sym.upper()}},
            {"term": {"expiry": expiry}},
            {"term": {"type.keyword": type_}},
            {"term": {"strike": strike}},
        ]}},
        size=1,
        sort=[{"timestamp": "desc"}],
        _source=["delta", "gamma", "theta", "vega", "rho", "iv"],
    )
    hits = r["hits"]["hits"]
    if not hits:
        raise HTTPException(404, "Option not found")
    s = hits[0]["_source"]
    return {k: round(float(s.get(k, 0)), 6) for k in ["delta", "gamma", "theta", "vega", "rho", "iv"]}

@router.get("/positions")
async def get_positions():
    if not positions_db:
        return []
    result = []
    for pos in positions_db.values():
        try:
            r = await es.search(
                index=INDEX,
                query={"bool": {"must": [
                    {"term": {"ticker.keyword": pos["symbol"]}},
                    {"term": {"expiry": pos["expiry"]}},
                    {"term": {"type.keyword": pos["type"]}},
                    {"term": {"strike": pos["strike"]}},
                ]}},
                size=1, sort=[{"timestamp": "desc"}],
                _source=["bid", "ask", "delta", "theta"],
            )
            hits = r["hits"]["hits"]
        except Exception:
            hits = []
        if hits:
            s     = hits[0]["_source"]
            last  = (float(s.get("bid", 0)) + float(s.get("ask", 0))) / 2
            delta = float(s.get("delta", 0))
            theta = float(s.get("theta", 0))
        else:
            last = pos["avgCost"]; delta = theta = 0.0

        qty      = pos["qty"]
        avg_cost = pos["avgCost"]
        side     = 1 if pos["direction"] == "buy" else -1
        pnl      = (last - avg_cost) * qty * 100 * side
        mkt_val  = last * qty * 100
        pnl_pct  = (pnl / abs(avg_cost * qty * 100) * 100) if avg_cost else 0.0
        result.append({
            "id": pos["id"], "symbol": pos["symbol"], "strike": pos["strike"],
            "expiry": pos["expiry"], "type": pos["type"], "qty": qty,
            "avgCost": avg_cost, "last": round(last, 2),
            "marketValue": round(mkt_val, 2), "pnl": round(pnl, 2),
            "pnlPct": round(pnl_pct, 2), "delta": round(delta, 4),
            "theta": round(theta, 4),
        })
    return result

@router.get("/portfolio/greeks")
async def get_portfolio_greeks():
    if not positions_db:
        return {"netDelta": 0.0, "netTheta": 0.0, "netVega": 0.0,
                "totalPnL": 0.0, "marginUsed": 0.0}
    net_delta = net_theta = net_vega = total_pnl = margin = 0.0
    for pos in positions_db.values():
        try:
            r = await es.search(
                index=INDEX,
                query={"bool": {"must": [
                    {"term": {"ticker.keyword": pos["symbol"]}},
                    {"term": {"expiry": pos["expiry"]}},
                    {"term": {"type.keyword": pos["type"]}},
                    {"term": {"strike": pos["strike"]}},
                ]}},
                size=1, sort=[{"timestamp": "desc"}],
                _source=["bid", "ask", "delta", "theta", "vega"],
            )
            hits = r["hits"]["hits"]
        except Exception:
            hits = []
        if hits:
            s = hits[0]["_source"]
            last  = (float(s.get("bid", 0)) + float(s.get("ask", 0))) / 2
            delta = float(s.get("delta", 0))
            theta = float(s.get("theta", 0))
            vega  = float(s.get("vega",  0))
        else:
            last = pos["avgCost"]; delta = theta = vega = 0.0
        qty  = pos["qty"]
        side = 1 if pos["direction"] == "buy" else -1
        net_delta += delta * qty * side
        net_theta += theta * qty * side
        net_vega  += vega  * qty * side
        total_pnl += (last - pos["avgCost"]) * qty * 100 * side
        margin    += pos["avgCost"] * qty * 100 * (0.2 if pos["direction"] == "sell" else 1.0)
    return {
        "netDelta":   round(net_delta, 4),
        "netTheta":   round(net_theta, 4),
        "netVega":    round(net_vega,  4),
        "totalPnL":   round(total_pnl, 2),
        "marginUsed": round(margin, 2),
    }

@router.post("/orders")
async def place_order(body: dict):
    oid       = str(uuid.uuid4())[:8].upper()
    sym       = str(body.get("symbol",    "")).upper()
    strike    = float(body.get("strike",  0) or 0)
    expiry    = str(body.get("expiry",    ""))
    opt_type  = str(body.get("type",      "call"))
    direction = str(body.get("direction", "buy"))
    qty       = int(body.get("qty",       1) or 1)
    price     = float(body.get("limitPrice", 0) or 0)
    if sym and strike and expiry and price > 0:
        positions_db[oid] = {
            "id": oid, "symbol": sym, "strike": strike, "expiry": expiry,
            "type": opt_type, "direction": direction, "qty": qty,
            "avgCost": price, "openedAt": date.today().isoformat(),
        }
    return {"orderId": oid, "status": "filled"}

@router.post("/positions/{pid}/close")
async def close_position(pid: str):
    pos = positions_db.pop(pid, None)
    if pos is None:
        raise HTTPException(404, "Position not found")
    trade_log.append({**pos, "closedAt": date.today().isoformat()})
    return {"status": "closed", "id": pid}

# ── Analytics endpoints ───────────────────────────────────────────────────────

@router.get("/analytics/iv-history/{sym}")
async def get_iv_history(sym: str):
    sym        = sym.upper()
    thirty_ago = (date.today() - timedelta(days=30)).isoformat()
    r = await es.search(
        index=INDEX,
        query={"bool": {"must": [
            {"term": {"ticker.keyword": sym}},
            {"term": {"type.keyword": "call"}},
            {"range": {"timestamp": {"gte": thirty_ago}}},
        ]}},
        aggs={"by_day": {
            "date_histogram": {"field": "timestamp", "calendar_interval": "day", "format": "yyyy-MM-dd"},
            "aggs": {"sample": {"top_hits": {
                "size": 100, "sort": [{"timestamp": "desc"}],
                "_source": ["iv", "strike", "underlying_price"],
            }}},
        }},
        size=0,
    )
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, _get_yf_blocking, sym)
    hv30 = info.get("hv30", 0.0)
    result = []
    for bucket in r["aggregations"]["by_day"]["buckets"]:
        hits = bucket["sample"]["hits"]["hits"]
        if not hits:
            continue
        underlying = float(hits[0]["_source"].get("underlying_price", 0))
        if underlying == 0:
            continue
        atm = min(hits, key=lambda h: abs(float(h["_source"].get("strike", 0)) - underlying))
        iv  = float(atm["_source"].get("iv", 0)) * 100
        if iv > 0:
            result.append({"date": bucket["key_as_string"], "iv": round(iv, 2), "hv30": hv30})
    return sorted(result, key=lambda x: x["date"])

@router.get("/analytics/flow/{sym}")
async def get_options_flow(sym: str):
    sym   = sym.upper()
    today = date.today().isoformat()
    r = await es.search(
        index=INDEX,
        query={"bool": {"must": [
            {"term": {"ticker.keyword": sym}},
            {"range": {"timestamp": {"gte": today}}},
        ]}},
        size=500, sort=[{"volume": "desc"}],
        _source=["strike", "expiry", "type", "volume", "bid", "ask"],
    )
    seen: dict[tuple, dict] = {}
    for hit in r["hits"]["hits"]:
        s   = hit["_source"]
        key = (float(s.get("strike", 0)), s.get("expiry", ""), s.get("type", "call"))
        if key not in seen:
            seen[key] = s
    top = sorted(seen.values(), key=lambda x: int(x.get("volume", 0) or 0), reverse=True)[:10]
    return [{
        "strike": float(s.get("strike", 0)),
        "expiry": s.get("expiry", ""),
        "type":   s.get("type",   "call"),
        "volume": int(s.get("volume", 0) or 0),
        "oi":     0,
        "bid":    round(float(s.get("bid", 0) or 0), 2),
        "ask":    round(float(s.get("ask", 0) or 0), 2),
    } for s in top]

async def _ticker_iv_rank(sym: str) -> dict | None:
    try:
        u = await _latest_underlying(sym)
        if not u:
            return None
        price    = float(u["underlying_price"])
        expiries = await _expiry_list(sym)
        if not expiries:
            return None
        r_st = await es.search(
            index=INDEX,
            query={"bool": {"must": [
                {"term": {"ticker.keyword": sym}},
                {"term": {"expiry": expiries[0]}},
                {"term": {"type.keyword": "call"}},
            ]}},
            aggs={"st": {"terms": {"field": "strike", "size": 300}}},
            size=0,
        )
        buckets = r_st["aggregations"]["st"]["buckets"]
        if not buckets:
            return None
        atm_k = min(buckets, key=lambda b: abs(b["key"] - price))["key"]
        thirty_ago = (date.today() - timedelta(days=30)).isoformat()
        r_cur, r_rng = await asyncio.gather(
            es.search(
                index=INDEX,
                query={"bool": {"must": [
                    {"term": {"ticker.keyword": sym}},
                    {"term": {"expiry": expiries[0]}},
                    {"term": {"type.keyword": "call"}},
                    {"term": {"strike": atm_k}},
                ]}},
                size=1, sort=[{"timestamp": "desc"}], _source=["iv"],
            ),
            es.search(
                index=INDEX,
                query={"bool": {"must": [
                    {"term": {"ticker.keyword": sym}},
                    {"term": {"type.keyword": "call"}},
                    {"range": {"timestamp": {"gte": thirty_ago}}},
                    {"range": {"strike": {"gte": atm_k * 0.95, "lte": atm_k * 1.05}}},
                ]}},
                aggs={"min_iv": {"min": {"field": "iv"}}, "max_iv": {"max": {"field": "iv"}}},
                size=0,
            ),
        )
        if not r_cur["hits"]["hits"]:
            return None
        cur_iv  = float(r_cur["hits"]["hits"][0]["_source"]["iv"]) * 100
        min_iv  = float(r_rng["aggregations"]["min_iv"]["value"] or cur_iv / 100) * 100
        max_iv  = float(r_rng["aggregations"]["max_iv"]["value"] or cur_iv / 100) * 100
        iv_rank = round(max(0.0, min(100.0,
            (cur_iv - min_iv) / (max_iv - min_iv) * 100 if max_iv > min_iv else 50.0
        )), 1)
        loop = asyncio.get_event_loop()
        info  = await loop.run_in_executor(None, _get_yf_blocking, sym)
        hv30  = info.get("hv30") or 1.0
        iv_rv = round(cur_iv / hv30, 2) if hv30 else 1.0
        if iv_rank > 65:
            sug = "Iron Condor" if iv_rank > 80 else "Short Strangle"
            rat = f"IV rank {iv_rank:.0f}% — elevated premium, sell strategies favored"
        elif iv_rank < 30:
            sug = "Long Straddle" if iv_rv < 0.9 else "Long Call / Put"
            rat = f"IV rank {iv_rank:.0f}% — cheap volatility, buy strategies favored"
        else:
            sug = "Bull Call Spread"
            rat = f"IV rank {iv_rank:.0f}% — neutral IV, directional spreads work well"
        return {"symbol": sym, "price": round(price, 2), "ivRank": iv_rank,
                "ivRv": iv_rv, "suggestion": sug, "rationale": rat}
    except Exception:
        return None

@router.get("/analytics/scanner")
async def get_scanner():
    results = await asyncio.gather(*[_ticker_iv_rank(s) for s in WATCHLIST])
    return [r for r in results if r is not None]

app.include_router(router)

# ── WebSocket ────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def ws_handler(ws: WebSocket):
    await ws.accept()
    symbols: list[str] = []

    async def push_quotes() -> None:
        for sym in symbols:
            try:
                u = await _latest_underlying(sym)
                if not u:
                    continue
                price = float(u["underlying_price"])
                prev  = float(u.get("underlying_prev_close") or price)
                ch    = price - prev
                await ws.send_text(json.dumps({
                    "type": "quote",
                    "payload": {
                        "symbol":    sym,
                        "price":     price,
                        "change":    round(ch, 2),
                        "changePct": round(ch / prev * 100, 2) if prev else 0.0,
                        "bid":       float(u.get("underlying_bid", price)),
                        "ask":       float(u.get("underlying_ask", price)),
                    },
                }))
            except Exception:
                pass

    try:
        while True:
            try:
                text = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
                msg  = json.loads(text)
                if msg.get("action") == "subscribe":
                    symbols = [s.upper() for s in msg.get("symbols", [])]
                    await push_quotes()
            except asyncio.TimeoutError:
                await push_quotes()
    except (WebSocketDisconnect, Exception):
        pass

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
