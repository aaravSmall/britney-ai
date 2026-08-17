"""
Stock search/quote/history data for the standalone stock detail feature
(app/routes/stocks.py) — separate from the agent's own price-only lookups
in app/services/market_data.py, but reusing that module's approach where
it applies:

- Search: Yahoo Finance's public, unauthenticated search endpoint
  (query1.finance.yahoo.com/v1/finance/search), called with plain httpx +
  a browser User-Agent — the exact same pattern market_data.py's
  _fetch_yahoo_stock_price already uses for prices.
- History: Yahoo's public, unauthenticated chart endpoint
  (query1.finance.yahoo.com/v8/finance/chart/{symbol}) — also already
  used by market_data.py, which only reads its `meta.regularMarketPrice`
  field; this reads the OHLCV series the same response already contains.
- Quote: needs fields (market cap, trailing P/E, average volume, dividend
  yield, sector, industry, a description) that Yahoo's own v7/finance/quote
  and quoteSummary endpoints used to expose for free, but as of this
  writing both return 401/"Invalid Crumb" for unauthenticated requests —
  confirmed by hand against the live API before writing this, not
  assumed. The `yfinance` library handles that crumb/cookie handshake
  internally and was verified (also by hand) to return all the needed
  fields in one call, so it's used here for quote() only — search() and
  history() stay on the plain-httpx path since that already works and
  matches the existing pattern.

yfinance's Ticker.info is a synchronous, blocking call (real network I/O
under the hood, no async support) — always run it via run_in_threadpool
so it doesn't block the event loop, the same class of bug fixed for the
sync OpenAI calls in agent/sentiment.py / app/ai/recommendation_engine.py
/ app/ai/chat_engine.py.

Mock/offline fallback follows the same convention as market_data.py and
agent/news_ingestion.py: never hard-fail on a network/API error, log
loudly which path was taken, and degrade to a small built-in demo
dataset when live data isn't reachable — reusing market_data.py's own
5-symbol mock_stocks universe (AAPL/MSFT/GOOGL/VOO/SPY) rather than
inventing a second one, so offline/dev-mode search, quote, and price
fallbacks all agree on the same demo symbols. An unknown ticker during a
live-data outage has no sensible mock to fall back to, so it surfaces as
"not found" the same as a genuinely invalid ticker — see quote()'s
docstring.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

import httpx
import yfinance as yf
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

_HEADERS = {"User-Agent": "Mozilla/5.0"}

SEARCH_TTL_SECONDS = 300  # user is typing; stale-tolerant, cuts Yahoo calls a lot
QUOTE_TTL_SECONDS = 60  # matches market_data.py's own price TTL
HISTORY_TTL_SECONDS = 300  # a closed candle doesn't change; the open one updates slowly

MIN_QUERY_LENGTH = 2
MAX_SEARCH_RESULTS = 10

# range key (as used by the dashboard's ChartRange — see
# frontend/lib/data/chart_range.dart's shortLabel values, reused verbatim
# so the frontend's existing range toggle can drive this later) -> Yahoo's
# own (range, interval) chart-endpoint params. Confirmed live for all
# five before writing this: each returns a sane, non-empty point count.
#
# "13W" is not one of the frontend's chart-range buttons — it's used
# internally by agent/classification.py's Gate A/C (trailing-13-week
# weekly closes for the growth-consistency and volatility checks; see
# docs/DISCOVERY_DESIGN.md §3). interval="1wk" already returns one
# candle per week directly, so no client-side resampling is needed.
# Left in this same public dict (rather than a private classification-
# only constant) since it's exercised through the exact same history()
# call/cache/route validation as every other range key, and the
# /stocks/{ticker}/history route already accepts any RANGE_TO_YAHOO key.
RANGE_TO_YAHOO: dict[str, tuple[str, str]] = {
    "1D": ("1d", "5m"),
    "1W": ("5d", "15m"),
    "30D": ("1mo", "1d"),
    "YTD": ("ytd", "1d"),
    "5Y": ("5y", "1wk"),
    "13W": ("3mo", "1wk"),
}

# Same known-symbol universe market_data.py's fetch_stock_price() mocks,
# reused verbatim (not reinvented) for search/quote fallback consistency.
_MOCK_SYMBOLS: list[dict[str, str]] = [
    {"ticker": "AAPL", "name": "Apple Inc.", "exchange": "NASDAQ"},
    {"ticker": "MSFT", "name": "Microsoft Corporation", "exchange": "NASDAQ"},
    {"ticker": "GOOGL", "name": "Alphabet Inc.", "exchange": "NASDAQ"},
    {"ticker": "VOO", "name": "Vanguard S&P 500 ETF", "exchange": "NYSEArca"},
    {"ticker": "SPY", "name": "SPDR S&P 500 ETF Trust", "exchange": "NYSEArca"},
]
_MOCK_PRICES: dict[str, float] = {
    "AAPL": 230.0,
    "MSFT": 420.0,
    "GOOGL": 175.0,
    "VOO": 520.0,
    "SPY": 590.0,
}

# Separately typed TTL caches (search/quote/history each cache a
# different value shape) — same {key: (expires_at, value)} pattern as
# market_data.py's price-only _cache.
_search_cache: dict[str, tuple[float, list[dict[str, str]]]] = {}
_quote_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}
_history_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _cache_get(cache: dict[str, tuple[float, Any]], key: str) -> Any | None:
    entry = cache.get(key)
    if not entry:
        return None
    expires, value = entry
    if time.time() > expires:
        del cache[key]
        return None
    return value


def _cache_set(cache: dict[str, tuple[float, Any]], key: str, value: Any, ttl: float) -> None:
    cache[key] = (time.time() + ttl, value)


def _mock_search(query: str) -> list[dict[str, str]]:
    q = query.lower()
    return [
        s for s in _MOCK_SYMBOLS if q in s["ticker"].lower() or q in s["name"].lower()
    ][:MAX_SEARCH_RESULTS]


async def search(query: str) -> list[dict[str, str]]:
    """Ticker/company-name search, capped to MAX_SEARCH_RESULTS. Empty or
    sub-MIN_QUERY_LENGTH queries return [] immediately without hitting
    Yahoo at all — the frontend calls this on every keystroke, so this
    (plus the TTL cache below) is what keeps that from hammering Yahoo's
    endpoint."""
    q = query.strip()
    if len(q) < MIN_QUERY_LENGTH:
        return []

    key = q.lower()
    cached = _cache_get(_search_cache, key)
    if cached is not None:
        return cached

    results: list[dict[str, str]] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": q, "quotesCount": MAX_SEARCH_RESULTS, "newsCount": 0},
                headers=_HEADERS,
            )
            r.raise_for_status()
            data = r.json()
        for item in data.get("quotes", []):
            ticker = item.get("symbol")
            name = item.get("longname") or item.get("shortname")
            exchange = item.get("exchDisp") or item.get("exchange")
            if not ticker or not name:
                continue
            results.append({"ticker": ticker, "name": name, "exchange": exchange or ""})
        results = results[:MAX_SEARCH_RESULTS]
        logger.info("real Yahoo search success — %d result(s) for %r.", len(results), q)
    except Exception as exc:
        logger.warning(
            "mock fallback (search error: %s: %s) — searching the offline demo "
            "symbol list instead of live Yahoo search for %r.",
            type(exc).__name__, exc, q,
        )
        results = _mock_search(q)

    _cache_set(_search_cache, key, results, SEARCH_TTL_SECONDS)
    return results


def _sync_fetch_quote_info(ticker: str) -> dict[str, Any]:
    """Blocking call — only ever invoke via run_in_threadpool."""
    return yf.Ticker(ticker).info


async def quote(ticker: str) -> dict[str, Any] | None:
    """Returns None for "not found" — the route maps that straight to a
    404, matching the requirement that an invalid ticker never leaks
    yfinance's own error as a 500 (the fetch itself is always wrapped in
    try/except below, regardless of ticker).

    Two different things can produce that same "no data" outcome, and
    they're kept distinct via `fetch_error` so the logs never confuse
    one for the other:
    - the fetch call raised (network error, timeout, Yahoo's crumb/
      cookie handshake blocking us, rate limiting, ...) — a real
      failure, logged at ERROR;
    - yfinance returned cleanly but with no usable price — a genuinely
      unknown/invalid ticker, logged at INFO.

    Either way, a ticker in the small demo universe (_MOCK_PRICES) still
    gets a real, labeled-as-mock quote instead of a 404 — the same
    mock-fallback convention market_data.py/news_ingestion.py use. An
    arbitrary (non-demo) ticker has no sensible quote to fabricate, so
    it 404s in both cases — but only the failure case logs it loudly,
    since that's the one worth alerting on (a real outage), not the
    other (someone just searched a bad ticker)."""
    key = ticker.upper()
    cached = _cache_get(_quote_cache, key)
    if cached is not None or key in _quote_cache:
        return cached

    info: dict[str, Any] = {}
    fetch_error: Exception | None = None
    try:
        info = await run_in_threadpool(_sync_fetch_quote_info, key)
    except Exception as exc:
        fetch_error = exc
        info = {}

    price = info.get("regularMarketPrice") or info.get("currentPrice")
    if price is None:
        if key in _MOCK_PRICES:
            # Same log line regardless of *why* live data is missing (a
            # raised exception and a clean-but-empty yfinance response
            # both land here) — either way the caller gets a real,
            # labeled-as-mock quote instead of a 404, which is what
            # matters for a symbol we actually know about.
            logger.warning(
                "mock fallback (no live data%s) — using the offline demo quote for %s.",
                f": {type(fetch_error).__name__}: {fetch_error}" if fetch_error else "",
                key,
            )
            result = {
                "ticker": key,
                "company_name": next(
                    s["name"] for s in _MOCK_SYMBOLS if s["ticker"] == key
                ),
                "price": _MOCK_PRICES[key],
                "change": None,
                "change_pct": None,
                "day_high": None,
                "day_low": None,
                "fifty_two_week_high": None,
                "fifty_two_week_low": None,
                "volume": None,
                "average_volume": None,
                "market_cap": None,
                "pe_ratio_trailing": None,
                "dividend_yield": None,
                "sector": None,
                "industry": None,
                "description": None,
            }
            _cache_set(_quote_cache, key, result, QUOTE_TTL_SECONDS)
            return result

        if fetch_error is not None:
            # Distinguish this from a genuine not-found: the fetch itself
            # blew up (network error, timeout, Yahoo's crumb/cookie
            # handshake blocking us, rate limiting, ...) for a ticker
            # that's not in the small demo set, so there's no fallback
            # quote to serve. Still surfaces as 404 to the caller (we
            # have nothing to return either way) but ERROR + a distinct
            # message keeps this from being confused with a confirmed-bad
            # ticker when grepping logs — a real outage should be loud.
            logger.error(
                "quote fetch FAILED for %s (%s: %s) — not a confirmed-invalid "
                "ticker, just no live data and no mock fallback available for it.",
                key, type(fetch_error).__name__, fetch_error,
            )
        else:
            logger.info(
                "quote not found for %s — yfinance returned no market data "
                "(ticker likely doesn't exist) and it's not in the demo set.",
                key,
            )
        _cache_set(_quote_cache, key, None, QUOTE_TTL_SECONDS)
        return None

    logger.info("real yfinance quote success for %s.", key)
    result = {
        "ticker": key,
        "company_name": info.get("longName") or info.get("shortName") or key,
        "price": float(price),
        "change": info.get("regularMarketChange"),
        "change_pct": info.get("regularMarketChangePercent"),
        "day_high": info.get("dayHigh"),
        "day_low": info.get("dayLow"),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
        "volume": info.get("volume"),
        "average_volume": info.get("averageVolume"),
        "market_cap": info.get("marketCap"),
        "pe_ratio_trailing": info.get("trailingPE"),
        "dividend_yield": info.get("dividendYield"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "description": info.get("longBusinessSummary"),
    }
    _cache_set(_quote_cache, key, result, QUOTE_TTL_SECONDS)
    return result


async def history(ticker: str, range_key: str) -> list[dict[str, Any]]:
    """OHLCV candles for range_key (one of RANGE_TO_YAHOO's keys — the
    route validates this before calling in). Returns [] (not a mock
    series) on failure: fabricating fake price history is more
    misleading than an honest empty state, and the frontend's chart
    already has a "not enough history" placeholder for exactly this
    (see dashboard_screen.dart's _SparseHistoryPlaceholder)."""
    key = f"{ticker.upper()}:{range_key}"
    cached = _cache_get(_history_cache, key)
    if cached is not None:
        return cached

    yahoo_range, interval = RANGE_TO_YAHOO[range_key]
    candles: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker.upper()}",
                params={"range": yahoo_range, "interval": interval},
                headers=_HEADERS,
            )
            r.raise_for_status()
            data = r.json()
        result = data["chart"]["result"]
        if not result:
            raise ValueError(f"no chart result for {ticker!r}")
        chart = result[0]
        timestamps = chart.get("timestamp") or []
        quote_arr = chart["indicators"]["quote"][0]
        opens = quote_arr.get("open") or []
        highs = quote_arr.get("high") or []
        lows = quote_arr.get("low") or []
        closes = quote_arr.get("close") or []
        volumes = quote_arr.get("volume") or []

        for i, ts in enumerate(timestamps):
            o, h, low, c = opens[i], highs[i], lows[i], closes[i]
            # Yahoo leaves gaps (pre/post-market, halts) as nulls in these
            # arrays — skip rather than emit a candle with missing OHLC.
            if o is None or h is None or low is None or c is None:
                continue
            candles.append(
                {
                    "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(),
                    "open": float(o),
                    "high": float(h),
                    "low": float(low),
                    "close": float(c),
                    "volume": int(volumes[i]) if i < len(volumes) and volumes[i] is not None else None,
                }
            )
        logger.info(
            "real Yahoo history success — %d candle(s) for %s range=%s.",
            len(candles), ticker.upper(), range_key,
        )
    except Exception as exc:
        logger.warning(
            "history fallback (empty) — could not fetch %s range=%s (%s: %s).",
            ticker.upper(), range_key, type(exc).__name__, exc,
        )
        candles = []

    _cache_set(_history_cache, key, candles, HISTORY_TTL_SECONDS)
    return candles
