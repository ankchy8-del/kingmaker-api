# main.py
import os, time, asyncio
from typing import Dict, Optional, List
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

APP = FastAPI()
APP.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

API_KEY = os.getenv("ALPHAVANTAGE_KEY", "")
MIN_GAP = int(os.getenv("MIN_GAP_SECONDS", "15"))          # 1 call each 15s (<= 5/min)
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "120"))     # reuse for 2 minutes

_cache: Dict[str, Dict[str, float]] = {}                   # {ticker: {"ts": epoch, "price": float}}
_last_call_ts = 0.0

def _cached(ticker: str) -> Optional[float]:
    item = _cache.get(ticker)
    if not item:
        return None
    if time.time() - item["ts"] <= CACHE_TTL:
        return item["price"]
    return None

def _remember(ticker: str, price: float) -> float:
    _cache[ticker] = {"ts": time.time(), "price": price}
    return price

async def _respect_rate_limit():
    global _last_call_ts
    wait = MIN_GAP - (time.time() - _last_call_ts)
    if wait > 0:
        await asyncio.sleep(wait)
    _last_call_ts = time.time()

async def alpha_price(ticker: str) -> Optional[float]:
    """Fetch one price from Alpha Vantage GLOBAL_QUOTE with rate limiting."""
    if not API_KEY:
        return None

    await _respect_rate_limit()

    url = "https://www.alphavantage.co/query"
    params = {"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": API_KEY}

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url, params=params)
        # Alpha Vantage returns HTTP 200 even on errors; look at JSON.
        data = r.json()

    # Too many calls -> they return {"Note": "...frequency..."}
    if isinstance(data, dict) and ("Note" in data or "Information" in data or "Error Message" in data):
        return None

    try:
        price = float(data["Global Quote"]["05. price"])
        return price
    except Exception:
        return None

@APP.get("/")
def root():
    return {"ok": True, "service": "kingmaker-api"}

@APP.get("/stock/{ticker}")
async def stock_quote(ticker: str):
    ticker = ticker.upper().strip()
    p = _cached(ticker)
    if p is not None:
        return {"ticker": ticker, "price": p, "cached": True}

    p = await alpha_price(ticker)
    if p is None:
        # keep returning “unavailable” instead of 503, so your app doesn’t show red errors
        raise HTTPException(status_code=200, detail="Price unavailable")
    return {"ticker": ticker, "price": _remember(ticker, p), "cached": False}

@APP.get("/batch")
async def batch(symbols: str):
    tickers: List[str] = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    out: Dict[str, Optional[float]] = {}
    # First return any cached values to be fast
    for t in tickers:
        out[t] = _cached(t)

    # Fetch missing ones respecting rate limit
    for t in tickers:
        if out[t] is not None:
            continue
        p = await alpha_price(t)
        if p is not None:
            out[t] = _remember(t, p)
        else:
            out[t] = None
    return out
