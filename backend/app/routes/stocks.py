"""Public stock search/quote/history endpoints — same no-auth posture as
the agent's public model-portfolio reads (app/routes/portfolios.py):
this is public market data, not anything user- or portfolio-specific.
Stocks only; crypto search/detail is out of scope for this pass (see
app/services/stock_data.py's module docstring)."""

from fastapi import APIRouter, HTTPException

from app.schemas.stocks import (
    StockHistoryResponse,
    StockQuote,
    StockSearchResult,
)
from app.services import stock_data

router = APIRouter(prefix="/stocks", tags=["stocks"])


@router.get("/search", response_model=list[StockSearchResult])
async def search_stocks(q: str = "") -> list[dict]:
    return await stock_data.search(q)


@router.get("/{ticker}/quote", response_model=StockQuote)
async def get_stock_quote(ticker: str) -> dict:
    result = await stock_data.quote(ticker)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker: {ticker.upper()}")
    return result


@router.get("/{ticker}/history", response_model=StockHistoryResponse)
async def get_stock_history(ticker: str, range: str = "30D") -> dict:
    range_key = range.upper()
    if range_key not in stock_data.RANGE_TO_YAHOO:
        valid = ", ".join(stock_data.RANGE_TO_YAHOO)
        raise HTTPException(
            status_code=422, detail=f"range must be one of: {valid} (got {range!r})"
        )
    candles = await stock_data.history(ticker, range_key)
    return {"ticker": ticker.upper(), "range": range_key, "candles": candles}
