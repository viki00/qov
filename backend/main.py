from fastapi import FastAPI, APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from elasticsearch import AsyncElasticsearch
import asyncio, asyncpg, json, os, time, uuid, warnings
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
DB_URL  = os.getenv("DB_URL", "postgresql://user:pass@timescaledb-svc:5432/finance")

es = AsyncElasticsearch(
    ES_HOST,
    basic_auth=(ES_USER, ES_PASS),
    verify_certs=False,
    ssl_show_warn=False,
)

pool: asyncpg.Pool | None = None

WATCHLIST = ["NVDA", "AAPL", "MSFT", "TSLA", "GOOGL", "AMZN", "SPY", "QQQ"]

# ── Lifecycle ────────────────────────────────────────────────────────────────

@app.on_event("startup")
async def _startup():
    global pool
    pool = await asyncpg.create_pool(DB_URL, min_size=2, max_size=10)
    await _init_schema()

@app.on_event("shutdown")
async def _shutdown():
    if pool:
        await pool.close()

async def _init_schema():
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_account (
                id              SERIAL PRIMARY KEY,
                starting_balance DECIMAL(12,2) NOT NULL DEFAULT 100000.00,
                cash_balance    DECIMAL(12,2) NOT NULL,
                created_at      TIMESTAMPTZ DEFAULT NOW()
            );
        """)
        await conn.execute("""
            INSERT INTO paper_account (starting_balance, cash_balance)
            SELECT 100000.00, 100000.00
            WHERE NOT EXISTS (SELECT 1 FROM paper_account);
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_positions (
                id          VARCHAR(8) PRIMARY KEY,
                symbol      VARCHAR(10)  NOT NULL,
                strike      DECIMAL(10,2) NOT NULL,
                expiry      DATE         NOT NULL,
                type        VARCHAR(4)   NOT NULL,
                direction   VARCHAR(4)   NOT NULL,
                qty         INT          NOT NULL,
                avg_cost    DECIMAL(10,4) NOT NULL,
                opened_at   TIMESTAMPTZ  DEFAULT NOW(),
                closed_at   TIMESTAMPTZ,
                close_price DECIMAL(10,4),
                realized_pnl DECIMAL(12,2),
                is_open     BOOLEAN      DEFAULT TRUE
            );
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_pnl_snapshots (
                id               BIGSERIAL,
                position_id      VARCHAR(8)    NOT NULL,
                snapshot_date    DATE          NOT NULL DEFAULT CURRENT_DATE,
                underlying_price DECIMAL(10,4),
                option_mid       DECIMAL(10,4),
                delta            DECIMAL(10,6),
                theta            DECIMAL(10,6),
                vega             DECIMAL(10,6),
                gamma            DECIMAL(10,6),
                iv               DECIMAL(10,6),
                created_at       TIMESTAMPTZ   DEFAULT NOW()
            );
        """)
        try:
            await conn.execute(
                "SELECT create_hypertable('paper_pnl_snapshots','created_at',if_not_exists=>TRUE);"
            )
        except Exception:
            pass

# ── yfinance cache ───────────────────────────────────────────────────────────

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
            "name":         info.get("shortName") or info.get("longName") or sym,
            "week52High":   float(info.get("fiftyTwoWeekHigh") or 0),
            "week52Low":    float(info.get("fiftyTwoWeekLow")  or 0),
            "pe":           round(float(info.get("trailingPE") or 0), 1),
            "marketCap":    _fmt_cap(float(info.get("marketCap") or 0)),
            "nextEarnings": earn,
            "hv30":         round(hv30, 1),
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
        size=1, sort=[{"timestamp": "desc"}],
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

async def _option_mid(sym: str, expiry: str, opt_type: str, strike: float) -> float:
    try:
        r = await es.search(
            index=INDEX,
            query={"bool": {"must": [
                {"term": {"ticker.keyword": sym}},
                {"term": {"expiry": expiry}},
                {"term": {"type.keyword": opt_type}},
                {"term": {"strike": strike}},
            ]}},
            size=1, sort=[{"timestamp": "desc"}], _source=["bid", "ask"],
        )
        hits = r["hits"]["hits"]
        if hits:
            s = hits[0]["_source"]
            return (float(s.get("bid", 0)) + float(s.get("ask", 0))) / 2
    except Exception:
        pass
    return 0.0

# ── Market data endpoints ─────────────────────────────────────────────────────

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
        "symbol": sym, "name": info["name"],
        "price": price, "change": round(ch, 2),
        "changePct": round(ch / prev * 100, 2) if prev else 0.0,
        "bid": float(u.get("underlying_bid", price)),
        "ask": float(u.get("underlying_ask", price)),
        "volume": _fmt_vol(int(u.get("underlying_volume", 0))),
        "open": float(u.get("underlying_open", 0)),
        "high": float(u.get("underlying_high", 0)),
        "low":  float(u.get("underlying_low",  0)),
        "week52High": info["week52High"], "week52Low": info["week52Low"],
        "ivRank": iv_rank, "ivRv": iv_rv, "hv30": info["hv30"],
        "iv": iv_pct, "pe": info["pe"],
        "marketCap": info["marketCap"], "nextEarnings": info["nextEarnings"],
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
        size=10000, sort=[{"timestamp": "desc"}],
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
async def get_greeks(sym: str, strike: float, expiry: str, type_: str = Query(..., alias="type")):
    r = await es.search(
        index=INDEX,
        query={"bool": {"must": [
            {"term": {"ticker.keyword": sym.upper()}},
            {"term": {"expiry": expiry}},
            {"term": {"type.keyword": type_}},
            {"term": {"strike": strike}},
        ]}},
        size=1, sort=[{"timestamp": "desc"}],
        _source=["delta", "gamma", "theta", "vega", "rho", "iv"],
    )
    hits = r["hits"]["hits"]
    if not hits:
        raise HTTPException(404, "Option not found")
    s = hits[0]["_source"]
    return {k: round(float(s.get(k, 0)), 6) for k in ["delta", "gamma", "theta", "vega", "rho", "iv"]}

# ── Paper trading — account ──────────────────────────────────────────────────

@router.get("/account")
async def get_account():
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id, starting_balance::float, cash_balance::float, created_at FROM paper_account ORDER BY id LIMIT 1"
        )
    if not row:
        raise HTTPException(500, "Account not initialized")
    return {
        "id": row["id"],
        "startingBalance": row["starting_balance"],
        "cashBalance":     row["cash_balance"],
        "createdAt":       row["created_at"].isoformat(),
    }

@router.post("/account/reset")
async def reset_account(body: dict):
    balance = float(body.get("balance", 100000.00))
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE paper_positions SET is_open=FALSE, closed_at=NOW() WHERE is_open=TRUE"
        )
        await conn.execute(
            "UPDATE paper_account SET cash_balance=$1, starting_balance=$1 "
            "WHERE id=(SELECT id FROM paper_account ORDER BY id LIMIT 1)", balance
        )
    return {"status": "reset", "balance": balance}

# ── Paper trading — orders ───────────────────────────────────────────────────

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

    if not (sym and strike and expiry and price > 0):
        raise HTTPException(400, "symbol, strike, expiry, limitPrice are required")

    cost = price * qty * 100
    cash_delta = -cost if direction == "buy" else cost

    async with pool.acquire() as conn:
        acc = await conn.fetchrow(
            "SELECT cash_balance::float FROM paper_account ORDER BY id LIMIT 1"
        )
        if direction == "buy" and acc["cash_balance"] < cost:
            raise HTTPException(400, f"Insufficient funds: need ${cost:,.2f}, have ${acc['cash_balance']:,.2f}")

        await conn.execute(
            "INSERT INTO paper_positions (id,symbol,strike,expiry,type,direction,qty,avg_cost) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
            oid, sym, strike, expiry, opt_type, direction, qty, price,
        )
        await conn.execute(
            "UPDATE paper_account SET cash_balance=cash_balance+$1 "
            "WHERE id=(SELECT id FROM paper_account ORDER BY id LIMIT 1)", cash_delta
        )
    return {"orderId": oid, "status": "filled", "cost": cost}

# ── Paper trading — positions ────────────────────────────────────────────────

@router.get("/positions")
async def get_positions():
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, symbol, strike::float, expiry::text, type, direction, qty, avg_cost::float "
            "FROM paper_positions WHERE is_open=TRUE ORDER BY opened_at DESC"
        )
    if not rows:
        return []
    result = []
    for pos in rows:
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
                _source=["bid", "ask", "delta", "theta", "gamma"],
            )
            hits = r["hits"]["hits"]
        except Exception:
            hits = []
        if hits:
            s     = hits[0]["_source"]
            last  = (float(s.get("bid", 0)) + float(s.get("ask", 0))) / 2
            delta = float(s.get("delta", 0))
            theta = float(s.get("theta", 0))
            gamma = float(s.get("gamma", 0))
        else:
            last = pos["avg_cost"]; delta = theta = gamma = 0.0
        qty      = pos["qty"]
        avg_cost = pos["avg_cost"]
        side     = 1 if pos["direction"] == "buy" else -1
        pnl      = (last - avg_cost) * qty * 100 * side
        mkt_val  = last * qty * 100
        pnl_pct  = pnl / abs(avg_cost * qty * 100) * 100 if avg_cost else 0.0
        result.append({
            "id": pos["id"], "symbol": pos["symbol"], "strike": pos["strike"],
            "expiry": pos["expiry"], "type": pos["type"], "direction": pos["direction"],
            "qty": qty, "avgCost": avg_cost, "last": round(last, 2),
            "marketValue": round(mkt_val, 2), "pnl": round(pnl, 2),
            "pnlPct": round(pnl_pct, 2), "delta": round(delta, 4),
            "theta": round(theta, 4), "gamma": round(gamma, 6),
        })
    return result

@router.get("/portfolio/greeks")
async def get_portfolio_greeks():
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, symbol, strike::float, expiry::text, type, direction, qty, avg_cost::float "
            "FROM paper_positions WHERE is_open=TRUE"
        )
        acc = await conn.fetchrow(
            "SELECT cash_balance::float FROM paper_account ORDER BY id LIMIT 1"
        )
    cash = acc["cash_balance"] if acc else 100000.0
    if not rows:
        return {"netDelta": 0.0, "netGamma": 0.0, "netTheta": 0.0, "netVega": 0.0,
                "totalPnL": 0.0, "marginUsed": 0.0, "cashBalance": cash}
    net_delta = net_gamma = net_theta = net_vega = total_pnl = margin = 0.0
    for pos in rows:
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
                _source=["bid", "ask", "delta", "theta", "vega", "gamma"],
            )
            hits = r["hits"]["hits"]
        except Exception:
            hits = []
        if hits:
            s     = hits[0]["_source"]
            last  = (float(s.get("bid", 0)) + float(s.get("ask", 0))) / 2
            delta = float(s.get("delta", 0))
            theta = float(s.get("theta", 0))
            vega  = float(s.get("vega",  0))
            gamma = float(s.get("gamma", 0))
        else:
            last = pos["avg_cost"]; delta = theta = vega = gamma = 0.0
        qty  = pos["qty"]
        side = 1 if pos["direction"] == "buy" else -1
        net_delta += delta * qty * side
        net_gamma += gamma * qty * side
        net_theta += theta * qty * side
        net_vega  += vega  * qty * side
        total_pnl += (last - pos["avg_cost"]) * qty * 100 * side
        margin    += pos["avg_cost"] * qty * 100 * (0.2 if pos["direction"] == "sell" else 1.0)
    return {
        "netDelta":   round(net_delta, 4),
        "netGamma":   round(net_gamma, 6),
        "netTheta":   round(net_theta, 4),
        "netVega":    round(net_vega,  4),
        "totalPnL":   round(total_pnl, 2),
        "marginUsed": round(margin, 2),
        "cashBalance": round(cash, 2),
    }

@router.post("/positions/{pid}/close")
async def close_position(pid: str):
    async with pool.acquire() as conn:
        pos = await conn.fetchrow(
            "SELECT id, symbol, strike::float, expiry::text, type, direction, qty, avg_cost::float "
            "FROM paper_positions WHERE id=$1 AND is_open=TRUE", pid
        )
    if not pos:
        raise HTTPException(404, "Position not found")

    close_price = await _option_mid(pos["symbol"], pos["expiry"], pos["type"], pos["strike"])
    if close_price == 0.0:
        close_price = pos["avg_cost"]

    qty  = pos["qty"]
    side = 1 if pos["direction"] == "buy" else -1
    realized_pnl = (close_price - pos["avg_cost"]) * qty * 100 * side
    cash_credit  = close_price * qty * 100 * side

    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE paper_positions SET is_open=FALSE, closed_at=NOW(), close_price=$1, realized_pnl=$2 WHERE id=$3",
            close_price, realized_pnl, pid,
        )
        await conn.execute(
            "UPDATE paper_account SET cash_balance=cash_balance+$1 "
            "WHERE id=(SELECT id FROM paper_account ORDER BY id LIMIT 1)", cash_credit
        )
    return {"status": "closed", "id": pid, "realizedPnL": round(realized_pnl, 2)}

# ── Paper trading — P&L snapshots ────────────────────────────────────────────

@router.post("/pnl/snapshot")
async def take_snapshot():
    """Capture today's Greeks + prices for all open positions. Idempotent per day."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, symbol, strike::float, expiry::text, type, direction, qty "
            "FROM paper_positions WHERE is_open=TRUE"
        )
    today  = date.today()
    snapped = 0
    for pos in rows:
        async with pool.acquire() as conn:
            existing = await conn.fetchval(
                "SELECT COUNT(*) FROM paper_pnl_snapshots WHERE position_id=$1 AND snapshot_date=$2",
                pos["id"], today,
            )
        if existing > 0:
            continue
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
                _source=["bid", "ask", "delta", "theta", "vega", "gamma", "iv", "underlying_price"],
            )
            hits = r["hits"]["hits"]
        except Exception:
            continue
        if not hits:
            continue
        s   = hits[0]["_source"]
        mid = (float(s.get("bid", 0)) + float(s.get("ask", 0))) / 2
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO paper_pnl_snapshots "
                "(position_id,snapshot_date,underlying_price,option_mid,delta,theta,vega,gamma,iv) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)",
                pos["id"], today,
                float(s.get("underlying_price", 0)), mid,
                float(s.get("delta", 0)), float(s.get("theta", 0)),
                float(s.get("vega",  0)), float(s.get("gamma", 0)),
                float(s.get("iv",    0)),
            )
        snapped += 1
    return {"status": "ok", "snapped": snapped}

@router.get("/pnl/daily")
async def get_daily_pnl():
    """Daily P&L decomposed into delta / theta / vega / residual attribution."""
    async with pool.acquire() as conn:
        rows = await conn.fetch("""
            SELECT s.position_id,
                   s.snapshot_date,
                   s.underlying_price::float,
                   s.option_mid::float,
                   s.delta::float,
                   s.theta::float,
                   s.vega::float,
                   s.gamma::float,
                   s.iv::float,
                   p.qty,
                   p.direction
            FROM paper_pnl_snapshots s
            JOIN paper_positions p ON p.id = s.position_id
            ORDER BY s.position_id, s.snapshot_date ASC
        """)
    if not rows:
        return []

    by_pos: dict[str, list] = {}
    for row in rows:
        pid = row["position_id"]
        if pid not in by_pos:
            by_pos[pid] = []
        by_pos[pid].append(dict(row))

    daily: dict[str, dict] = {}
    for pid, snaps in by_pos.items():
        if len(snaps) < 2:
            continue
        for i in range(1, len(snaps)):
            prev = snaps[i - 1]
            curr = snaps[i]
            d    = curr["snapshot_date"].isoformat()
            qty  = prev["qty"]
            side = 1 if prev["direction"] == "buy" else -1

            und_chg    = curr["underlying_price"] - prev["underlying_price"]
            iv_chg     = curr["iv"] - prev["iv"]
            actual_pnl = (curr["option_mid"] - prev["option_mid"]) * qty * 100 * side
            delta_pnl  = prev["delta"] * und_chg * qty * 100 * side
            theta_pnl  = prev["theta"] * 1 * qty * 100 * side   # 1 calendar day
            vega_pnl   = prev["vega"]  * iv_chg  * qty * 100 * side
            residual   = actual_pnl - delta_pnl - theta_pnl - vega_pnl

            if d not in daily:
                daily[d] = {"date": d, "deltaPnl": 0.0, "thetaPnl": 0.0,
                            "vegaPnl": 0.0, "residual": 0.0, "total": 0.0}
            daily[d]["deltaPnl"]  += delta_pnl
            daily[d]["thetaPnl"]  += theta_pnl
            daily[d]["vegaPnl"]   += vega_pnl
            daily[d]["residual"]  += residual
            daily[d]["total"]     += actual_pnl

    result = sorted(daily.values(), key=lambda x: x["date"])
    for row in result:
        for k in ["deltaPnl", "thetaPnl", "vegaPnl", "residual", "total"]:
            row[k] = round(row[k], 2)
    return result

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
        "strike": float(s.get("strike", 0)), "expiry": s.get("expiry", ""),
        "type":   s.get("type",   "call"),   "volume": int(s.get("volume", 0) or 0),
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
        atm_k      = min(buckets, key=lambda b: abs(b["key"] - price))["key"]
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
                        "symbol":    sym, "price": price,
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
