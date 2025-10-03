from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict
import httpx, time, csv, io

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- Simple in-memory state ----------
class Holding(BaseModel):
    ticker: str
    qty: float
    avg: float

portfolio: List[Dict] = []

# cache for quotes: { "TICKER": {"price": float, "ts": epoch} }
_cache: Dict[str, Dict] = {}
CACHE_TTL = 60  # seconds

# ---------- Helper: robust Yahoo quote ----------
from fastapi import HTTPException
import httpx, time, csv, io

CACHE_TTL = 60
_cache: dict[str, dict] = {}

async def _yahoo_v7_price(t: str, client: httpx.AsyncClient) -> float | None:
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={t}"
    r = await client.get(url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
    if r.status_code == 429:  # rate limit
        return None
    r.raise_for_status()
    data = r.json()
    result = (data.get("quoteResponse", {}).get("result") or [])
    if not result:
        return None
    q = result[0]
    price = q.get("regularMarketPrice") or q.get("previousClose")
    return float(price) if price else None

async def _yahoo_v8_price(t: str, client: httpx.AsyncClient) -> float | None:
    # last close from intraday chart
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{t}?range=1d&interval=5m"
    r = await client.get(url, headers={"User-Agent":"Mozilla/5.0","Accept":"application/json"})
    if r.status_code == 429:
        return None
    r.raise_for_status()
    data = r.json()
    try:
        closes = (data["chart"]["result"][0]["indicators"]["quote"][0]["close"] or [])
        # pick last non-null close
        for v in reversed(closes):
            if v:
                return float(v)
    except Exception:
        return None
    return None

async def _stooq_price(t: str, client: httpx.AsyncClient) -> float | None:
    # Stooq needs lowercase and sometimes different tickers; try direct
    sym = t.lower()
    url = f"https://stooq.com/q/l/?s={sym}&f=sd2t2ohlcv&h&e=csv"
    r = await client.get(url, headers={"User-Agent":"Mozilla/5.0"})
    r.raise_for_status()
    txt = r.text.strip()
    if "N/D" in txt:  # no data
        return None
    f = io.StringIO(txt)
    reader = csv.DictReader(f)
    row = next(reader, None)
    if not row:
        return None
    close = row.get("Close")
    return float(close) if close and close not in ("N/D", "0") else None

async def get_last_price(ticker: str) -> float:
    t = (ticker or "").upper().strip()
    if not t:
        raise HTTPException(status_code=400, detail="Ticker required")

    now = time.time()
    hit = _cache.get(t)
    if hit and now - hit["ts"] < CACHE_TTL:
        return hit["price"]

    async with httpx.AsyncClient(timeout=8) as client:
        # 1) Yahoo v7
        try:
            p = await _yahoo_v7_price(t, client)
            if p and p > 0:
                _cache[t] = {"price": p, "ts": now}
                return p
        except httpx.TimeoutException:
            pass
        except Exception:
            pass

        # 2) Yahoo v8 chart
        try:
            p = await _yahoo_v8_price(t, client)
            if p and p > 0:
                _cache[t] = {"price": p, "ts": now}
                return p
        except httpx.TimeoutException:
            pass
        except Exception:
            pass

        # 3) Stooq CSV
        try:
            p = await _stooq_price(t, client)
            if p and p > 0:
                _cache[t] = {"price": p, "ts": now}
                return p
        except httpx.TimeoutException:
            pass
        except Exception:
            pass

    # fallback to cache if we had anything
    if hit:
        return hit["price"]

    raise HTTPException(status_code=502, detail="No price from providers")

# ---- route using the new helper ----
@app.get("/stock/{ticker}")
async def stock_quote(ticker: str):
    price = await get_last_price(ticker)
    return {"ticker": ticker.upper(), "price": price}
# ---------- Routes ----------
@app.get("/")
def root():
    return {"message": "King Maker API is running!"}

@app.get("/stock/{ticker}")
async def stock_quote(ticker: str):
    price = await yahoo_last_price(ticker)
    return {"ticker": ticker.upper(), "price": price}

@app.post("/api/portfolio/sync")
def sync_portfolio(holdings: List[Holding]):
    global portfolio
    merged: Dict[str, Dict] = {}
    for h in holdings:
        T = h.ticker.upper()
        if T in merged:
            prev = merged[T]
            qty_new = prev["qty"] + h.qty
            avg_new = (prev["qty"] * prev["avg"] + h.qty * h.avg) / qty_new
            merged[T] = {"ticker": T, "qty": qty_new, "avg": avg_new}
        else:
            merged[T] = {"ticker": T, "qty": h.qty, "avg": h.avg}
    portfolio = list(merged.values())
    return {"status": "ok", "count": len(portfolio)}

@app.get("/api/portfolio")
async def get_portfolio():
    out = []
    for p in portfolio:
        last = await yahoo_last_price(p["ticker"])
        out.append({**p, "last": last})
    return out

@app.get("/api/signals/live")
async def get_signals():
    res = []
    for p in portfolio:
        last = await yahoo_last_price(p["ticker"])
        avg = p.get("avg") or 0.0
        chg = (last - avg) / avg if avg else 0.0
        if chg >= 0.15:
            res.append({"id": f"{p['ticker']}-trim", "ticker": p["ticker"], "action": "TRIM",
                        "size_pct": 20, "note": "Strong run; reduce risk", "confidence": 0.8})
        elif chg <= -0.07:
            res.append({"id": f"{p['ticker']}-add", "ticker": p["ticker"], "action": "BUY",
                        "size_pct": 10, "note": "Buy the dip; 10% trailing stop", "confidence": 0.74})
    return res or [{"id": "mu-default", "ticker": "MU", "action": "BUY",
                    "size_pct": 10, "note": "Momentum setup", "confidence": 0.75}]
