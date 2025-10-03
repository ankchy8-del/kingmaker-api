from fastapi import FastAPI
from pydantic import BaseModel
from typing import List
import yfinance as yf

app = FastAPI()

# --- CORS: allow your app to call this API from the browser ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],      # you can tighten later to your domain(s)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
# ---------------------------------------------------------------

class Holding(BaseModel):
    ticker: str
    qty: float
    avg: float

portfolio: list[dict] = []

@app.get("/")
def root():
    return {"message": "King Maker API is running!"}

@app.get("/api/portfolio")
def get_portfolio():
    out = []
    for p in portfolio:
        try:
            t = yf.Ticker(p["ticker"])
            last = float(t.fast_info.last_price or t.fast_info.previous_close or 0)
        except Exception:
            last = 0.0
        out.append({**p, "last": last})
    return out

@app.post("/api/portfolio/sync")
def sync_portfolio(holdings: List[Holding]):
    global portfolio
    portfolio = [h.dict() for h in holdings]
    return {"status": "ok", "count": len(portfolio)}

@app.get("/api/signals/live")
def get_signals():
    res = []
    for p in portfolio:
        last = p.get("last") or p["avg"]
        avg = p.get("avg") or 0
        chg = (last - avg) / avg if avg else 0
        if chg >= 0.15:
            res.append({"id": f"{p['ticker']}-trim", "ticker": p["ticker"], "action": "TRIM",
                        "size_pct": 20, "note": "Strong run; reduce risk", "confidence": 0.8})
        elif chg <= -0.07:
            res.append({"id": f"{p['ticker']}-add", "ticker": p["ticker"], "action": "BUY",
                        "size_pct": 10, "note": "Buy the dip; 10% trailing stop", "confidence": 0.74})
    return res or [{"id": "mu-default", "ticker": "MU", "action": "BUY",
                    "size_pct": 10, "note": "20D breakout", "confidence": 0.75}]
