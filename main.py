from fastapi import FastAPI, HTTPException
import httpx
import os
import asyncio

@app.get("/debug/raw")
async def debug_raw(symbol: str):
    """TEMP: return exactly what Alpha Vantage returned (first 2KB)"""
    import httpx, os
    API_KEY = os.getenv("ALPHA_VANTAGE_KEY", "")
    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={API_KEY}"
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(url)
    text = r.text
    return {"status": r.status_code, "length": len(text), "preview": text[:2000]}

app = FastAPI()

API_KEY = os.getenv("ALPHA_VANTAGE_KEY")
MIN_GAP_SECONDS = int(os.getenv("MIN_GAP_SECONDS", "15"))
CACHE_TTL_SECONDS = int(os.getenv("CACHE_TTL_SECONDS", "120"))

cache = {}
last_call_time = 0


@app.get("/health")
def health():
    return {"ok": True}


async def fetch_price(symbol: str):
    global last_call_time

    # Use cache
    if symbol in cache and (asyncio.get_event_loop().time() - cache[symbol]['time'] < CACHE_TTL_SECONDS):
        return cache[symbol]['price']

    # Respect rate limit
    since_last = asyncio.get_event_loop().time() - last_call_time
    if since_last < MIN_GAP_SECONDS:
        await asyncio.sleep(MIN_GAP_SECONDS - since_last)

    url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={symbol}&apikey={API_KEY}"

    async with httpx.AsyncClient() as client:
        r = await client.get(url)
        data = r.json()

    last_call_time = asyncio.get_event_loop().time()

    try:
        price = float(data["Global Quote"]["05. price"])
    except Exception:
        price = None

    cache[symbol] = {"price": price, "time": asyncio.get_event_loop().time()}
    return price


@app.get("/stock/{symbol}")
async def get_stock(symbol: str):
    price = await fetch_price(symbol)
    if price is None:
        raise HTTPException(status_code=404, detail="Price unavailable")
    return {"symbol": symbol, "price": price}


@app.get("/batch")
async def get_batch(symbols: str):
    result = {}
    for s in symbols.split(","):
        result[s] = await fetch_price(s.strip())
    return result
