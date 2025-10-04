# main.py
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
import os, asyncio, time
import httpx

app = FastAPI(title="King Maker API")

# CORS so your Expo app can call this
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ALPHA_KEY = os.getenv("ALPHA_VANTAGE_KEY")  # set this in Render
BASE = "https://www.alphavantage.co/query"

# Simple in-memory cache to reduce API calls and avoid rate limits
_CACHE: dict[str, tuple[float, float]] = {}  # {ticker_upper: (expiry_ts, price)}
TTL = 60  # seconds

async def fetch_price_alpha(client: httpx.AsyncClient, ticker: str) -> float | None:
    """
    Use Alpha Vantage GLOBAL_QUOTE to fetch a single latest price.
    Returns float price or None if not available.
    """
    if not ALPHA_KEY:
        raise HTTPException(status_code=500, detail="Missing ALPHA_VANTAGE_KEY")

    # cache
    key = ticker.upper()
    now = time.time()
    if key in _CACHE:
        exp, px = _CACHE[key]
        if now < exp:
            return px

    params = {
        "function": "GLOBAL_QUOTE",
        "symbol": key,
        "apikey": ALPHA_KEY,
    }
    r = await client.get(BASE, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    # Expected structure:
    # {"Global Quote": {"01. symbol":"SMCI","05. price":"..."}}
    quote = data.get("Global Quote") or {}
    price_str = quote.get("05. price")

    if not price_str:
        return None

    try:
        price = float(price_str)
    except ValueError:
        return None

    _CACHE[key] = (now + TTL, price)
    return price


@app.get("/")
async def root():
    return {"message": "King Maker API is running!"}


@app.get("/stock/{ticker}")
async def stock_quote(ticker: str):
    try:
        async with httpx.AsyncClient() as client:
            px = await fetch_price_alpha(client, ticker)
        if px is None:
            raise HTTPException(status_code=404, detail="Price unavailable")
        return {"symbol": ticker.upper(), "price": px}
    except httpx.HTTPError as e:
        raise HTTPException(status_code=502, detail=f"Upstream error: {str(e)}")


@app.get("/batch")
async def batch_quotes(symbols: str = Query(..., description="Comma-separated: e.g. SMCI,MU,TSLA")):
    # Alpha Vantage free plan doesn’t have a true batch endpoint,
    # so we fire multiple GLOBAL_QUOTE requests (with caching + concurrency).
    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="No symbols provided")

    out: dict[str, float | None] = {}

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[fetch_price_alpha(client, t) for t in tickers],
            return_exceptions=True
        )

    for t, res in zip(tickers, results):
        if isinstance(res, Exception):
            out[t] = None
        else:
            out[t] = res

    return out
