import os, time, json
from typing import Dict, Optional, List
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ALPHA_KEY = (os.getenv("ALPHA_VANTAGE_KEY") or "").strip()
MIN_GAP = int(os.getenv("MIN_GAP_SECONDS") or 15)
CACHE_TTL = int(os.getenv("CACHE_TTL_SECONDS") or 120)

print(f"[boot] ALPHA_VANTAGE_KEY present? {'YES' if bool(ALPHA_KEY) else 'NO'}")
print(f"[boot] MIN_GAP_SECONDS={MIN_GAP}, CACHE_TTL_SECONDS={CACHE_TTL}")

_last_call_ts = 0.0
_cache: Dict[str, Dict] = {}

def _cache_get(sym: str) -> Optional[float]:
    item = _cache.get(sym.upper())
    if not item: return None
    if time.time() - item["ts"] > CACHE_TTL:
        return None
    return item["price"]

def _cache_put(sym: str, price: Optional[float]):
    _cache[sym.upper()] = {"price": price, "ts": time.time()}

async def _respect_gap():
    global _last_call_ts
    now = time.time()
    delta = now - _last_call_ts
    if delta < MIN_GAP:
        await asyncio_sleep = __import__("asyncio").sleep
        await asyncio_sleep(MIN_GAP - delta)
    _last_call_ts = time.time()

async def _alpha_global_quote(symbol: str) -> Dict:
    if not ALPHA_KEY:
        raise HTTPException(status_code=500, detail="Backend missing ALPHA_VANTAGE_KEY")
    await _respect_gap()
    url = "https://www.alphavantage.co/query"
    params = {"function": "GLOBAL_QUOTE", "symbol": symbol.upper(), "apikey": ALPHA_KEY}
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        return r.json()

def _extract_price(payload: Dict) -> Optional[float]:
    # Handle “Note” (rate limit), “Information”, empty payloads
    if "Note" in payload or "Information" in payload:
        return None
    gq = payload.get("Global Quote") or payload.get("GlobalQuote") or {}
    raw = gq.get("05. price") or gq.get("05-price") or gq.get("price")
    try:
        return float(raw) if raw not in (None, "") else None
    except Exception:
        return None

@app.get("/health")
def health():
    return {"ok": True}

@app.get("/")
def root():
    return {"ok": True, "service": "kingmaker-api"}

@app.get("/alpha_raw")
async def alpha_raw(symbol: str = Query(..., min_length=1)):
    """Debug endpoint: see the raw Alpha Vantage JSON."""
    data = await _alpha_global_quote(symbol)
    return data

@app.get("/stock/{symbol}")
async def stock(symbol: str):
    # cache first
    cached = _cache_get(symbol)
    if cached is not None:
        return {"ticker": symbol.upper(), "price": cached, "cached": True}

    data = await _alpha_global_quote(symbol)
    price = _extract_price(data)

    if price is None:
        # Distinguish rate-limit vs no data
        if "Note" in data or "Information" in data:
            raise HTTPException(status_code=503, detail="Upstream rate limited")
        raise HTTPException(status_code=404, detail="Price unavailable")

    _cache_put(symbol, price)
    return {"ticker": symbol.upper(), "price": price, "cached": False}

@app.get("/batch")
async def batch(symbols: str):
    syms: List[str] = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    out = {}
    for s in syms:
        cached = _cache_get(s)
        if cached is not None:
            out[s] = cached
            continue
        data = await _alpha_global_quote(s)
        price = _extract_price(data)
        if price is None:
            out[s] = None
        else:
            _cache_put(s, price)
            out[s] = price
    return out
