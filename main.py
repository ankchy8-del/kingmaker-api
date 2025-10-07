from __future__ import annotations

import os
import asyncio
from typing import Dict, Optional

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# ---------- configuration from environment ----------
API_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "300"))          # seconds to keep a price
MIN_GAP = int(os.getenv("MIN_GAP_SECONDS", "60"))               # min seconds between API calls

# ---------- app ----------
app = FastAPI(title="kingmaker-api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- simple in-memory cache & rate spacing ----------
cache: Dict[str, Dict[str, float | Optional[float]]] = {}
last_call_time: float = 0.0
lock = asyncio.Lock()


async def fetch_price(symbol: str) -> Optional[float]:
    """Fetch price from Alpha Vantage GLOBAL_QUOTE."""
    global last_call_time

    async with lock:
        # honor free-tier spacing
        now = asyncio.get_event_loop().time()
        to_wait = last_call_time + MIN_GAP - now
        if to_wait > 0:
            await asyncio.sleep(to_wait)

        url = (
            "https://www.alphavantage.co/query"
            f"?function=GLOBAL_QUOTE&symbol={symbol}&apikey={API_KEY}"
        )

        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.get(url)
            data = r.json()

        last_call_time = asyncio.get_event_loop().time()

    # Alpha Vantage often returns {"Note": "..."} when throttled
    if isinstance(data, dict) and ("Note" in data or "Error Message" in data):
        # cache the miss so we don't hammer
        cache[symbol] = {"price": None, "time": asyncio.get_event_loop().time()}
        return None

    try:
        price_str = data["Global Quote"]["05. price"]
        return float(price_str)
    except Exception:
        return None


async def get_price(symbol: str) -> Optional[float]:
    """Get price with TTL cache."""
    now = asyncio.get_event_loop().time()
    ent = cache.get(symbol)
    if ent and now - float(ent["time"]) < CACHE_TTL:
        return ent["price"]  # may be None if last call was throttled

    p = await fetch_price(symbol)
    cache[symbol] = {"price": p, "time": now}
    return p


# ---------------- routes ----------------
@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/")
async def root():
    return {"ok": True, "service": "kingmaker-api"}

@app.get("/stock/{symbol}")
async def stock(symbol: str):
    symbol = symbol.upper().strip()
    price = await get_price(symbol)
    if price is None:
        raise HTTPException(status_code=503, detail="Price unavailable")
    return {"symbol": symbol, "price": price}

@app.get("/batch")
async def batch(symbols: str):
    out: Dict[str, Optional[float]] = {}
    for raw in symbols.split(","):
        sym = raw.upper().strip()
        if not sym:
            continue
        out[sym] = await get_price(sym)
    return out

# ---------- TEMP debug endpoint (place AFTER app is defined!) ----------
@app.get("/debug/raw")
async def debug_raw(symbol: str):
    """Return the raw text from Alpha Vantage (first 2KB) to diagnose rate limits."""
    url = (
        "https://www.alphavantage.co/query"
        f"?function=GLOBAL_QUOTE&symbol={symbol}&apikey={API_KEY}"
    )
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url)
    return {"status": r.status_code, "length": len(r.text), "preview": r.text[:2000]}
