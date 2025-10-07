# main.py — FastAPI backend using Finnhub (free) with caching & gentle rate limiting
import os, time
from typing import Dict, Tuple, Optional, List
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx

FINNHUB_KEY = os.getenv("FINNHUB_KEY", "").strip()
if not FINNHUB_KEY:
    raise RuntimeError("Set FINNHUB_KEY in Render → Environment.")

CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS", "30"))          # serve cached value for up to 30s
MIN_GAP   = float(os.getenv("MIN_GAP_SECONDS", "5"))            # min seconds between queries per symbol

app = FastAPI(title="kingmaker-api")

# CORS: allow your Expo app & local dev
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],           # or narrow to your app origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# in-memory cache
# symbol -> (price, epoch_seconds, source)
_cache: Dict[str, Tuple[float, float, str]] = {}
_last_hit: Dict[str, float] = {}             # rate control per symbol

def _now() -> float:
    return time.time()

async def _finnhub_quote(symbol: str) -> float:
    """Call Finnhub quote endpoint, return current price (float)."""
    url = "https://finnhub.io/api/v1/quote"
    params = {"symbol": symbol.upper(), "token": FINNHUB_KEY}
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
        r = await client.get(url, params=params)
    if r.status_code != 200:
        raise HTTPException(status_code=503, detail=f"Upstream status {r.status_code}")
    data = r.json()
    # Finnhub returns: { c: current, h: high, l: low, o: open, pc: prevClose, t: timestamp }
    price = data.get("c")
    if price is None or price == 0:
        # 0 means no real-time for this symbol on your plan; treat as unavailable
        raise HTTPException(status_code=404, detail="Price unavailable")
    return float(price)

async def _get_price(symbol: str) -> Tuple[float, bool, str]:
    """
    Returns (price, from_cache, source).
    Respects MIN_GAP to avoid hammering upstream and serves CACHE_TTL.
    """
    s = symbol.upper()
    now = _now()

    # serve warm cache if fresh enough
    if s in _cache:
        price, ts, src = _cache[s]
        if now - ts <= CACHE_TTL:
            return price, True, src

    # gentle per-symbol spacing (avoids tripping provider limits)
    last = _last_hit.get(s, 0.0)
    if now - last < MIN_GAP and s in _cache:
        price, ts, src = _cache[s]
        return price, True, src

    # call upstream
    price = await _finnhub_quote(s)
    _cache[s] = (price, now, "finnhub")
    _last_hit[s] = now
    return price, False, "finnhub"

@app.get("/health")
async def health():
    return {"ok": True}

@app.get("/")
async def root():
    return {"ok": True, "service": "kingmaker-api"}

@app.get("/stock/{symbol}")
async def stock(symbol: str):
    price, cached, source = await _get_price(symbol)
    return {
        "ticker": symbol.upper(),
        "price": price,
        "cached": cached,
        "source": source,
        "ttl": CACHE_TTL,
    }

@app.get("/batch")
async def batch(symbols: str = Query(..., description="Comma-separated symbols, e.g. IBM,MSFT,TSLA")):
    out: Dict[str, Optional[float]] = {}
    for raw in symbols.split(","):
        s = raw.strip().upper()
        if not s:
            continue
        try:
            price, _, _ = await _get_price(s)
            out[s] = price
        except HTTPException:
            out[s] = None
    return out

# optional: peek upstream (helps debugging)
@app.get("/debug/raw")
async def debug_raw(symbol: str):
    url = "https://finnhub.io/api/v1/quote"
    params = {"symbol": symbol.upper(), "token": FINNHUB_KEY}
    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0)) as client:
        r = await client.get(url, params=params)
    # return brief summary to avoid huge bodies
    body = r.text
    preview = body[:300]
    return {"status": r.status_code, "length": len(body), "preview": preview}
