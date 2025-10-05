# main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import os, time, httpx

ALPHA_KEY = os.getenv("ALPHAVANTAGE_KEY", "").strip()
BASE = "https://www.alphavantage.co/query"

app = FastAPI()

# CORS for your phone + Expo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- very small in-memory cache to ease rate limits ---
_cache = {}  # key: ticker, value: (ts, price)

async def fetch_alpha_price(ticker: str) -> float | None:
    # return cached within 60s
    now = time.time()
    hit = _cache.get(ticker.upper())
    if hit and now - hit[0] < 60:
        return hit[1]

    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": ticker.upper(),
        "apikey": ALPHA_KEY,
    }
    async with httpx.AsyncClient(timeout=12) as client:
        r = await client.get(BASE, params=params)
        r.raise_for_status()
        data = r.json()

    q = data.get("Global Quote") or data.get("GlobalQuote") or {}
    price_str = q.get("05. price") or q.get("price")
    try:
        price = float(price_str) if price_str else None
    except Exception:
        price = None

    if price is not None:
        _cache[ticker.upper()] = (now, price)
    return price

@app.get("/")
async def root():
    return {"ok": True, "service": "kingmaker-api"}

@app.get("/stock/{ticker}")
async def stock(ticker: str):
    price = await fetch_alpha_price(ticker)
    if price is None:
        # upstream limit or no data
        raise HTTPException(status_code=503, detail="Price unavailable")
    return {"ticker": ticker.upper(), "price": price}

@app.get("/batch")
async def batch(symbols: str):
    out = {}
    for t in [s.strip() for s in symbols.split(",") if s.strip()]:
        try:
            p = await fetch_alpha_price(t)
            out[t.upper()] = p
            # gentle spacing for free tier (5/min, 25/day)
            await httpx.AsyncClient().aclose()  # no-op, just yield
            time.sleep(0.5)
        except Exception:
            out[t.upper()] = None
    return out
