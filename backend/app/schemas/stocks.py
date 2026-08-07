"""Response models for GET /stocks/* (search/quote/history) — stocks only
for this pass; field names are kept asset-generic (no "shares"-specific
naming) so a future crypto router could reuse the same shapes without a
redesign, but nothing crypto-specific is built here."""

from pydantic import BaseModel


class StockSearchResult(BaseModel):
    ticker: str
    name: str
    exchange: str


class StockQuote(BaseModel):
    ticker: str
    company_name: str
    price: float
    change: float | None = None
    change_pct: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    volume: int | None = None
    average_volume: int | None = None
    market_cap: float | None = None
    pe_ratio_trailing: float | None = None
    dividend_yield: float | None = None
    sector: str | None = None
    industry: str | None = None
    description: str | None = None


class StockHistoryPoint(BaseModel):
    timestamp: str  # ISO 8601, UTC
    open: float
    high: float
    low: float
    close: float
    volume: int | None = None


class StockHistoryResponse(BaseModel):
    ticker: str
    range: str
    candles: list[StockHistoryPoint]
