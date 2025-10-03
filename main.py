from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Create FastAPI app
app = FastAPI()

# Allow your mobile app (any origin) to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # You can later restrict this to your frontend domain
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Test endpoint
@app.get("/")
def root():
    return {"message": "King Maker API is running!"}

# Stock endpoint (example: /stock/SMCI)
@app.get("/stock/{ticker}")
def get_stock(ticker: str):
    # Mock response for now (we’ll later wire in real stock data)
    return {"ticker": ticker, "price": 123.45, "signal": "BUY"}
