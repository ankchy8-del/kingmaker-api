from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
import httpx

app = FastAPI()

# CORS so the app can call the API from browser/phone
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Models / in-memory state ----
class Holding(BaseModel):
    ticker: str
    qty: float
    avg: float

portfolio: list[dict] = []  # replace with DB later

# ---- Helpers ----
async def yahoo_last_price(ticker: str) -> float:
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={ticker}"
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.json()
        q = (data.get("quoteResponse", {}).get("result") or [{}])[0]
        return float(q.get("regularMarketPrice") or q.get("previousClose") or 0.0)

# ---- Routes ----
@app.get("/")
def root():
    return {"message": "King Maker API is running!"}

@app.get("/stock/{ticker}")
async def stock_quote(ticker: str):
    price = await yahoo_last_price(ticker)
    return {"ticker": ticker.upper(), "price": price}

@app.post("/api/portfolio/sync")
def sync_portfolio(holdings: List[Holding]):
    # Upsert/merge by ticker
    global portfolio
    merged = {}
    for h in holdings:
        T = h.ticker.upper()
        if T in merged:
            old = merged[T]
            qty_new = old["qty"] + h.qty
            avg_new = (old["qty"] * old["avg"] + h.qty * h.avg) / qty_new
            merged[T] = {"ticker": T, "qty": qty_new, "avg": avg_new}
        else:
            merged[T] = {"ticker": T, "qty": h.qty, "avg": h.avg}
    portfolio = list(merged.values())
    return {"status": "ok", "count": len(portfolio)}

@app.get("/api/portfolio")
async def get_portfolio():
    out = []
    for p in portfolio:
        last = await yahoo_last_price(p["ticker"])
        out.append({**p, "last": last})
    return out

@app.get("/api/signals/live")
async def get_signals():
    # Super simple rules — gets you going
    res = []
    for p in portfolio:
        last = await yahoo_last_price(p["ticker"])
        avg = p.get("avg") or 0.0
        chg = (last - avg) / avg if avg else 0.0
        if chg >= 0.15:
            res.append({"id": f"{p['ticker']}-trim", "ticker": p["ticker"], "action": "TRIM",
                        "size_pct": 20, "note": "Strong run; reduce risk", "confidence": 0.8})
        elif chg <= -0.07:
            res.append({"id": f"{p['ticker']}-add", "ticker": p["ticker"], "action": "BUY",
                        "size_pct": 10, "note": "Buy the dip; 10% trailing stop", "confidence": 0.74})
    return res or [
        {"id": "mu-default", "ticker": "MU", "action": "BUY",
         "size_pct": 10, "note": "Momentum setup", "confidence": 0.75}
    ]
