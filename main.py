# main.py  (repo root)
import os
import time
from typing import Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

# ----- Environment configuration -----
ALPHA_KEY = os.getenv("ALPHA_VANTAGE_KEY", "").strip()
if not ALPHA_KEY:
    raise RuntimeError("Missing env var ALPHA_VANTAGE_KEY")

# gentle defaults; can be overridden in Render Environment
MIN_GAP_SECONDS = int(os.getenv("MIN_GAP_SECONDS", "15"))     # min time between upstream calls
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "120"))  # keep a fresh price for 2 mins

# ----- App & CORS (so Expo can call it) -----
app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----- simple in-memory cache & rate guard -----
_cache: Dict[str, Dict[str, float]] = {}  # {ticker: {"price": float, "ts": epoch}}
_last_call_ts: float = 0.0

async def _alpha_price(ticker: str) -> Optional[float]:
    """Call Alpha Vantage GLOBAL_QUOTE for one ticker, respecting a minimal spacing between calls."""
    global _last_call_ts

    # throttle upstream calls
    now = time.time()
    gap = now - _last_call_ts
    if gap < MIN_GAP_SECONDS:
        await _sleep(MIN_GAP_SECONDS - gap)

    url = (
        "https://www.alphavantage.co/query"
        f"?function=GLOBAL_QUOTE&symbol={ticker}&apikey={ALPHA_KEY}"
    )
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(url)
        if r.status_code == 200:
            data = r.json() or {}
            quote = data.get("Global Quote") or {}
            # Alpha returns price string under "05. price"
            raw = quote.get("05. price")
            try:
                price = float(raw) if raw is not None else None
            except Exception:
                price = None
        else:
            price = None

    _last_call_ts = time.time()
    return price

async def _sleep(seconds: float):
    # tiny awaitable sleep helper without importing asyncio.sleep multiple times
    import asyncio
    await asyncio.sleep(max(0.0, seconds))

def _cache_get(ticker: str) -> Optional[float]:
    row = _cache.get(ticker)
    if not row:
        return None
    if time.time() - row["ts"] <= CACHE_TTL_SECONDS:
        return float(row["price"])
    # expired
    _cache.pop(ticker, None)
    return None

def _cache_set(ticker: str, price: Optional[float]):
    if price is None:
        return
    _cache[ticker] = {"price": float(price), "ts": time.time()}

# ---------- Routes ----------
@app.get("/")
def root():
    return {"ok": True, "service": "kingmaker-api"}

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/stock/{ticker}")
async def stock_quote(ticker: str):
    t = ticker.upper().strip()
    # cached?
    price = _cache_get(t)
    if price is not None:
        return {"ticker": t, "price": price, "cached": True}

    # fetch
    price = await _alpha_price(t)
    if price is None:
        raise HTTPException(status_code=503, detail="Price unavailable")
    _cache_set(t, price)
    return {"ticker": t, "price": price, "cached": False}

@app.get("/batch")
async def batch_quotes(symbols: str = Query(..., description="Comma separated tickers")):
    # normalize tickers
    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    out: Dict[str, Optional[float]] = {}

    # Try cache first
    need_fetch = []
    for t in tickers:
        c = _cache_get(t)
        if c is not None:
            out[t] = c
        else:
            out[t] = None
            need_fetch.append(t)

    # Fetch missing ones sequentially to respect free limits
    for t in need_fetch:
        price = await _alpha_price(t)
        if price is not None:
            _cache_set(t, price)
            out[t] = price
        else:
            out[t] = None

    return out
