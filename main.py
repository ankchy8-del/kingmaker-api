from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import httpx

@app.get("/stock/{ticker}")
async def stock_quote(ticker: str):
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
        q = (data.get("quoteResponse", {}).get("result") or [{}])[0]
        return {
            "ticker": ticker.upper(),
            "price": float(q.get("regularMarketPrice") or q.get("previousClose") or 0.0),
            "change": q.get("regularMarketChange"),
            "changePercent": q.get("regularMarketChangePercent"),
            "currency": q.get("currency"),
            "time": q.get("regularMarketTime"),
        }

app = FastAPI()

# Allow calls from your web/mobile app (relax now, tighten later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class Holding(BaseModel):
    ticker: str
    qty: float
    avg: float

# In-memory store (replace with DB later)
portfolio: list[dict] = []

@app.get("/")
def root():
    return {"message": "King Maker API is running!"}

async def yahoo_last_price(ticker: str) -> float:
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
        result = (data.get("quoteResponse", {}).get("result") or [{}])[0]
        return float(result.get("regularMarketPrice") or result.get("previousClose") or 0.0)

@app.get("/api/portfolio")
async def get_portfolio():
    out = []
    for p in portfolio:
        last = await yahoo_last_price(p["ticker"])
        out.append({**p, "last": last})
    return out

@app.post("/api/portfolio/sync")
def sync_portfolio(holdings: List[Holding]):
    global portfolio
    portfolio = [h.dict() for h in holdings]
    return {"status": "ok", "count": len(portfolio)}

@app.get("/api/signals/live")
async def get_signals():
    res = []
    for p in portfolio:
        last = await yahoo_last_price(p["ticker"]) or 0.0
        avg = p.get("avg") or 0.0
        chg = (last - avg) / avg if avg else 0.0
        if chg >= 0.15:
            res.append({"id": f"{p['ticker']}-trim", "ticker": p["ticker"], "action": "TRIM",
                        "size_pct": 20, "note": "Strong run; reduce risk", "confidence": 0.8})
        elif chg <= -0.07:
            res.append({"id": f"{p['ticker']}-add", "ticker": p["ticker"], "action": "BUY",
                        "size_pct": 10, "note": "Buy the dip; 10% trailing stop", "confidence": 0.74})
    return res or [{"id": "mu-default", "ticker": "MU", "action": "BUY",
                    "size_pct": 10, "note": "20D breakout", "confidence": 0.75}]
