# main.py
import time
from typing import Dict, Any, List, Optional
import httpx
import csv
import io

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# CORS so the phone can call it
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Upstreams ----
YAHOO_URL = "https://query1.finance.yahoo.com/v7/finance/quote?symbols={}"        # supports comma list
STOOQ_URL = "https://stooq.com/q/l/?s={}&f=sd2t2ohlcv&h&e=csv"                    # supports comma list, CSV

# ---- Cache (60s) ----
CACHE: Dict[str, Dict[str, Any]] = {}   # ticker -> {"price": float, "ts": epoch}
TTL = 60  # seconds


# ---------- Yahoo helpers ----------
async def yahoo_fetch(symbols: List[str]) -> Dict[str, Optional[float]]:
    url = YAHOO_URL.format(",".join(symbols))
    out: Dict[str, Optional[float]] = {s: None for s in symbols}
    async with httpx.AsyncClient(timeout=8.0) as client:
        for attempt in range(4):
            r = await client.get(url)
            if r.status_code == 200:
                try:
                    data = r.json()
                    results = data.get("quoteResponse", {}).get("result", [])
                    for item in results:
                        sym = (item.get("symbol") or "").upper()
                        price = item.get("regularMarketPrice")
                        if sym and price is not None:
                            out[sym] = float(price)
                    return out
                except Exception:
                    # structure changed
                    return out
            elif r.status_code in (429, 500, 502, 503, 504):
                time.sleep(0.6 * (attempt + 1))
                continue
            else:
                # hard error -> return Nones so caller can try fallback
                return out
        # retries exhausted
        return out


# Replace _parse_stooq_csv and stooq_fetch with these:
def _parse_stooq_csv(text: str) -> Dict[str, Optional[float]]:
    """
    Stooq CSV columns: Symbol,Date,Time,Open,High,Low,Close,Volume
    Returns dict {UPPER_SYMBOL: price or None}
    """
    out: Dict[str, Optional[float]] = {}
    f = io.StringIO(text)
    reader = csv.DictReader(f)
    for row in reader:
        sym = (row.get("Symbol") or "").upper()
        close = row.get("Close")
        if sym:
            # Close is "N/D" when not available
            try:
                out[sym] = None if (close is None or close == "N/D") else float(close)
            except Exception:
                out[sym] = None
    return out

async def stooq_fetch(symbols: List[str]) -> Dict[str, Optional[float]]:
    """
    Stooq expects US tickers like aapl.us, tsla.us, mu.us, smci.us
    We’ll append .us for plain A–Z tickers.
    """
    def to_stooq_symbol(s: str) -> str:
        s_low = s.lower()
        # If it already has a dot (exchange suffix), leave it; else add .us
        return s_low if "." in s_low else f"{s_low}.us"

    stooq_syms = [to_stooq_symbol(s) for s in symbols]
    url = STOOQ_URL.format(",".join(stooq_syms))

    out: Dict[str, Optional[float]] = {s: None for s in symbols}
    async with httpx.AsyncClient(timeout=8.0) as client:
        try:
            r = await client.get(url)
            if r.status_code == 200:
                parsed = _parse_stooq_csv(r.text)  # keys like TSLA.US, MU.US
                # map back to the original symbols (upper without .US)
                for original in symbols:
                    stooq_key = (to_stooq_symbol(original)).upper()  # TSLA.US
                    val = parsed.get(stooq_key)
                    out[original] = val if isinstance(val, (int, float)) else None
                return out
            else:
                return out
        except Exception:
            return out


# ---------- API ----------
@app.get("/")
async def root():
    return {"message": "King Maker API is running!"}

@app.get("/stock/{ticker}")
async def stock_quote(ticker: str):
    t = ticker.upper()
    now = time.time()

    # cache
    if t in CACHE and (now - CACHE[t]["ts"]) < TTL:
        return {"ticker": t, "price": CACHE[t]["price"], "cached": True}

    # try Yahoo then fallback to Stooq
    y = await yahoo_fetch([t])
    price = y.get(t)
    if price is None:
        s = await stooq_fetch([t])
        price = s.get(t)

    if price is None:
        # last resort: say unknown, don't crash
        raise HTTPException(status_code=502, detail="Price unavailable")

    CACHE[t] = {"price": price, "ts": now}
    return {"ticker": t, "price": price, "cached": False}

@app.get("/batch")
async def batch_quotes(symbols: str):
    """
    GET /batch?symbols=SMCI,MU,TSLA
    Returns map like {"SMCI": 49.1, "MU": 176.3, "TSLA": 255.0}
    Uses cache first, then Yahoo multi, then Stooq to fill gaps.
    """
    if not symbols:
        raise HTTPException(status_code=400, detail="symbols required")

    req: List[str] = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not req:
        raise HTTPException(status_code=400, detail="no valid symbols")

    now = time.time()
    out: Dict[str, Optional[float]] = {}
    to_fetch: List[str] = []

    # 1) cache
    for t in req:
        if t in CACHE and (now - CACHE[t]["ts"]) < TTL:
            out[t] = CACHE[t]["price"]
        else:
            to_fetch.append(t)

    if to_fetch:
        # 2) Yahoo for all missing
        y = await yahoo_fetch(to_fetch)
        # update out + find still-missing
        still: List[str] = []
        for t in to_fetch:
            if y.get(t) is not None:
                out[t] = y[t]
                CACHE[t] = {"price": y[t], "ts": now}
            else:
                still.append(t)

        # 3) Fallback: Stooq for the rest
        if still:
            s = await stooq_fetch(still)
            for t in still:
                if s.get(t) is not None:
                    out[t] = s[t]
                    CACHE[t] = {"price": s[t], "ts": now}
                else:
                    out[t] = None  # couldn't get it now

    # Ensure every requested symbol appears
    for t in req:
        out.setdefault(t, None)

    return out
