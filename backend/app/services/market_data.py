"""
Market data with in-memory TTL cache.
Uses CoinGecko for crypto; mock or Alpaca-ready path for stocks.
"""

import time
from typing import Any

import httpx

from app.config import get_settings

# Simple process-local cache: symbol -> (expires_at, price)
_cache: dict[str, tuple[float, float]] = {}
TTL_SECONDS = 60


def _get_cached(symbol: str) -> float | None:
    entry = _cache.get(symbol.upper())
    if not entry:
        return None
    expires, price = entry
    if time.time() > expires:
        del _cache[symbol.upper()]
        return None
    return price


def _set_cached(symbol: str, price: float) -> None:
    _cache[symbol.upper()] = (time.time() + TTL_SECONDS, price)


async def fetch_crypto_price_usd(symbol: str) -> float | None:
    """
    symbol: e.g. BTC, ETH (maps to coingecko ids simplified for MVP).
    """
    key = f"CRYPTO:{symbol.upper()}"
    c = _get_cached(key)
    if c is not None:
        return c

    mapping = {"BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana"}
    cid = mapping.get(symbol.upper(), "bitcoin")
    url = f"https://api.coingecko.com/api/v3/simple/price?ids={cid}&vs_currencies=usd"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(url)
            r.raise_for_status()
            data: dict[str, Any] = r.json()
            price = float(data[cid]["usd"])
            _set_cached(key, price)
            return price
        except Exception:
            # Fallback mock
            mock = {"BTC": 98000.0, "ETH": 3500.0, "SOL": 180.0}
            price = mock.get(symbol.upper(), 100.0)
            _set_cached(key, price)
            return price


async def _fetch_alpaca_stock_price(symbol: str, settings: Any) -> float | None:
    """Paid/production path: Alpaca Market Data API (separate host from the
    paper-trading order API in settings.alpaca_base_url)."""
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_secret_key,
    }
    url = f"https://data.alpaca.markets/v2/stocks/{symbol.upper()}/trades/latest"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(url, headers=headers)
            r.raise_for_status()
            data = r.json()
            return float(data["trade"]["p"])
        except Exception:
            return None


async def _fetch_yahoo_stock_price(symbol: str) -> float | None:
    """Free path: Yahoo Finance's public (unofficial, unauthenticated) quote
    endpoint. No signup/key required — good enough for an MVP, but
    undocumented and can change or rate-limit without notice."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol.upper()}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            r = await client.get(
                url,
                params={"range": "1d", "interval": "1m"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            r.raise_for_status()
            data = r.json()
            meta = data["chart"]["result"][0]["meta"]
            price = meta.get("regularMarketPrice")
            return float(price) if price is not None else None
        except Exception:
            return None


async def fetch_stock_price(symbol: str) -> float | None:
    """
    Live quote with graceful degradation: Alpaca (if paid keys configured)
    -> free Yahoo Finance endpoint -> static mock as a last resort.
    """
    key = f"STOCK:{symbol.upper()}"
    c = _get_cached(key)
    if c is not None:
        return c

    settings = get_settings()
    if settings.alpaca_api_key and settings.alpaca_secret_key:
        price = await _fetch_alpaca_stock_price(symbol, settings)
        if price is not None:
            _set_cached(key, price)
            return price

    price = await _fetch_yahoo_stock_price(symbol)
    if price is not None:
        _set_cached(key, price)
        return price

    # Last-resort mock, only reached if both live sources fail (e.g. offline).
    mock_stocks = {
        "AAPL": 230.0,
        "MSFT": 420.0,
        "GOOGL": 175.0,
        "VOO": 520.0,
        "SPY": 590.0,
    }
    price = mock_stocks.get(symbol.upper(), 100.0)
    _set_cached(key, price)
    return price


async def get_price_for_holding(symbol: str, asset_type: str) -> float | None:
    if asset_type == "crypto":
        return await fetch_crypto_price_usd(symbol)
    return await fetch_stock_price(symbol)
