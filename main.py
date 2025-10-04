# main.py
import time
from typing import Dict, Any, List

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS for dev and the mobile app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

YAHOO_URL = "https://query1.finance.yahoo.com/v7/finance/quote?symbols={}"

# Simple in-memory cache (per process, 60s)
CACHE: Dict[str, Dict[str, Any]] = {}   # ticker -> {"price": float, "ts": epoch}
TTL = 60  # seconds


async def yahoo_last_price(ticker: str) -> float:
    """Fetch single ticker with retries and robust parsing."""
    url = YAHOO_URL.format(ticker)
    async with httpx.AsyncClient(timeout=8.0) as client:
        for attempt in range(4):
            r = await client.get(url)
            if r.status_code == 200:
                try:
                    data = r.json()
                    results = data.get("quoteResponse", {}).get("result", [])
                    if not results:
                        raise ValueError("empty result")
                    price = results[0].get("regularMarketPrice")
                    if price is None:
                        raise ValueError("missing price")
                    return float(price)
                except Exception:
                    # Parsing/structure error
                    raise HTTPException(status_code=502, detail="Quote parse failed")
            elif r.status_code in (429, 500, 502, 503, 504):
                # Backoff and retry
                time.sleep(0.6 * (attempt + 1))
                continue
            else:
                raise HTTPException(status_code=r.status_code, detail=f"Yahoo error {r.status_code}")
        raise HTTPException(status_code=429, detail="Upstream rate limited")


@app.get("/")
async def root():
    return {"message": "King Maker API is running!"}


@app.get("/stock/{ticker}")
async def stock_quote(ticker: str):
    t = ticker.upper()
    now = time.time()
    if t in CACHE and (now - CACHE[t]["ts"]) < TTL:
        return {"ticker": t, "price": CACHE[t]["price"], "cached": True}
    price = await yahoo_last_price(t)
    CACHE[t] = {"price": price, "ts": now}
    return {"ticker": t, "price": price, "cached": False}


@app.get("/batch")
async def batch_quotes(symbols: str):
    """
    GET /batch?symbols=SMCI,MU,TSLA
    Returns: {"SMCI": 49.12, "MU": 176.35, "TSLA": 255.02}
    Uses cache for fresh tickers; fetches missing ones via ONE Yahoo call with retries.
    """
    if not symbols:
        raise HTTPException(status_code=400, detail="symbols required")

    tickers: List[str] = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="no valid symbols")

    now = time.time()
    out: Dict[str, Any] = {}
    to_fetch: List[str] = []

    # Serve what we can from cache
    for t in tickers:
        if t in CACHE and (now - CACHE[t]["ts"]) < TTL:
            out[t] = CACHE[t]["price"]
        else:
            to_fetch.append(t)

    if to_fetch:
        url = YAHOO_URL.format(",".join(to_fetch))
        async with httpx.AsyncClient(timeout=8.0) as client:
            for attempt in range(4):
                r = await client.get(url)
                if r.status_code == 200:
                    try:
                        data = r.json()
                        results = data.get("quoteResponse", {}).get("result", [])
                        got: Dict[str, float] = {}
                        for item in results:
                            sym = (item.get("symbol") or "").upper()
                            price = item.get("regularMarketPrice")
                            if sym and price is not None:
                                got[sym] = float(price)

                        # Update cache and output; if missing, return None (no crash)
                        for t in to_fetch:
                            if t in got:
                                CACHE[t] = {"price": got[t], "ts": now}
                                out[t] = got[t]
                            else:
                                out[t] = None
                        break
                    except Exception:
                        # Parsing/structure error: set None for those we tried
                        for t in to_fetch:
                            out.setdefault(t, None)
                        break
                elif r.status_code in (429, 500, 502, 503, 504):
                    time.sleep(0.6 * (attempt + 1))
                    continue
                else:
                    # Hard error from Yahoo: set None for unfetched, but do not crash
                    for t in to_fetch:
                        out.setdefault(t, None)
                    break
            else:
                # Exhausted retries: mark missing as None
                for t in to_fetch:
                    out.setdefault(t, None)

    # Ensure every requested ticker has a key
    for t in tickers:
        out.setdefault(t, None)

    return out
