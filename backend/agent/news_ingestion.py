"""
Standalone news ingestion for the britney.ai trading agent.

Pulls company news from Finnhub's free tier for the tickers in the three
target portfolios (conservative/moderate/aggressive), throttles calls to
respect Finnhub's free-tier ~60-calls/min limit, and dedupes articles
already delivered in a prior run via a small local sqlite cache.

Standalone — deliberately doesn't import app.database/app.models, so it
can be exercised without booting FastAPI or a real database:

    cd backend && python -m agent.news_ingestion
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.config import get_settings

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"

# Finnhub's free tier allows ~60 calls/min; stay under that with headroom
# in case something else in the process is also calling Finnhub.
DEFAULT_CALLS_PER_MINUTE = 50

# Mirrors the three risk buckets in app/ai/recommendation_engine.py's mock
# fallback (conservative == low risk, moderate == medium, aggressive ==
# high). Crypto tickers are listed for completeness but skipped by news
# ingestion — Finnhub's free company-news endpoint only covers equities/ETFs.
TARGET_PORTFOLIOS: dict[str, list[tuple[str, str]]] = {
    "conservative": [("VOO", "stock"), ("BND", "stock"), ("BTC", "crypto")],
    "moderate": [("SPY", "stock"), ("MSFT", "stock"), ("ETH", "crypto")],
    "aggressive": [("QQQ", "stock"), ("AAPL", "stock"), ("SOL", "crypto")],
}


def target_stock_tickers() -> list[str]:
    """Unique, order-stable stock/ETF tickers across all three target
    portfolios — the only ones Finnhub's company-news endpoint covers."""
    seen: set[str] = set()
    tickers: list[str] = []
    for assets in TARGET_PORTFOLIOS.values():
        for symbol, asset_type in assets:
            if asset_type != "stock" or symbol in seen:
                continue
            seen.add(symbol)
            tickers.append(symbol)
    return tickers


@dataclass
class NewsArticle:
    ticker: str
    headline: str
    summary: str
    source: str
    url: str
    published_at: datetime
    article_id: str

    def to_dict(self) -> dict:
        d = asdict(self)
        d["published_at"] = self.published_at.isoformat()
        return d


class SeenArticleStore:
    """Tiny sqlite-backed dedupe cache: article_id -> first-fetched
    timestamp. Kept separate from the main app DB on purpose (see module
    docstring) — gitignored like every other *.sqlite3 file in this repo."""

    def __init__(self, db_path: str | Path | None = None):
        self._path = str(db_path or Path(__file__).parent / ".news_seen.sqlite3")
        self._conn = sqlite3.connect(self._path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS seen_articles (
                article_id TEXT PRIMARY KEY,
                fetched_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def has_seen(self, article_id: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM seen_articles WHERE article_id = ?", (article_id,)
        ).fetchone()
        return row is not None

    def mark_seen(self, article_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO seen_articles (article_id, fetched_at) VALUES (?, ?)",
            (article_id, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


class RateLimiter:
    """Sliding-window throttle: `acquire()` blocks (async sleep) just long
    enough that no more than `calls_per_minute` calls go out in any 60s
    window, so a run over many tickers can't blow through Finnhub's free
    tier even if called back-to-back on a schedule."""

    def __init__(self, calls_per_minute: int = DEFAULT_CALLS_PER_MINUTE):
        self._calls_per_minute = calls_per_minute
        self._call_times: list[float] = []

    async def acquire(self) -> None:
        now = time.monotonic()
        window_start = now - 60
        self._call_times = [t for t in self._call_times if t > window_start]
        if len(self._call_times) >= self._calls_per_minute:
            sleep_for = 60 - (now - self._call_times[0])
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
        self._call_times.append(time.monotonic())


def _mock_articles(ticker: str) -> list[dict]:
    """Deterministic offline fallback — same spirit as the OpenAI mock in
    app/ai/recommendation_engine.py and chat_engine.py, so this module
    works with no FINNHUB_API_KEY set."""
    now = datetime.now(timezone.utc)
    return [
        {
            "id": f"{ticker}-mock-1",
            "headline": f"{ticker} steady ahead of next earnings report",
            "summary": (
                "[Offline mode — add FINNHUB_API_KEY for real news] Mock "
                f"placeholder article for {ticker}."
            ),
            "source": "mock",
            "url": "",
            "datetime": int(now.timestamp()),
        },
        {
            "id": f"{ticker}-mock-2",
            "headline": f"Analysts weigh in on {ticker}'s sector outlook",
            "summary": (
                "[Offline mode — add FINNHUB_API_KEY for real news] Second "
                f"mock placeholder article for {ticker}."
            ),
            "source": "mock",
            "url": "",
            "datetime": int((now - timedelta(hours=6)).timestamp()),
        },
    ]


def _normalize(ticker: str, raw: dict, *, provider: str) -> NewsArticle:
    ts = raw.get("datetime")
    published_at = (
        datetime.fromtimestamp(ts, tz=timezone.utc)
        if ts
        else datetime.now(timezone.utc)
    )
    return NewsArticle(
        ticker=ticker,
        headline=raw.get("headline", ""),
        summary=raw.get("summary", ""),
        source=raw.get("source") or provider,
        url=raw.get("url", ""),
        published_at=published_at,
        article_id=f"{provider}:{raw.get('id')}",
    )


async def _fetch_ticker_news_finnhub(
    client: httpx.AsyncClient,
    ticker: str,
    api_key: str,
    limiter: RateLimiter,
    days_back: int,
) -> list[dict]:
    to_date = datetime.now(timezone.utc).date()
    from_date = to_date - timedelta(days=days_back)
    await limiter.acquire()
    resp = await client.get(
        f"{FINNHUB_BASE_URL}/company-news",
        params={
            "symbol": ticker,
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "token": api_key,
        },
        timeout=10.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


async def fetch_news(
    tickers: list[str] | None = None,
    *,
    days_back: int = 2,
    store: SeenArticleStore | None = None,
    calls_per_minute: int = DEFAULT_CALLS_PER_MINUTE,
) -> list[NewsArticle]:
    """Fetch, normalize, and dedupe company news for `tickers` (defaults to
    every stock/ETF ticker across the three target portfolios).

    Uses Finnhub's free /company-news endpoint when FINNHUB_API_KEY is
    configured; otherwise returns deterministic mock articles so this can
    be exercised offline. Articles already returned by a previous call
    (tracked in `store`, persisted across runs) are skipped.
    """
    settings = get_settings()
    tickers = tickers or target_stock_tickers()
    store = store or SeenArticleStore()
    results: list[NewsArticle] = []

    if not settings.finnhub_api_key:
        for ticker in tickers:
            for raw in _mock_articles(ticker):
                article = _normalize(ticker, raw, provider="mock")
                if store.has_seen(article.article_id):
                    continue
                results.append(article)
                store.mark_seen(article.article_id)
        return results

    limiter = RateLimiter(calls_per_minute)
    async with httpx.AsyncClient() as client:
        for ticker in tickers:
            try:
                raw_articles = await _fetch_ticker_news_finnhub(
                    client, ticker, settings.finnhub_api_key, limiter, days_back
                )
            except httpx.HTTPError:
                # Skip this ticker for this run; the next scheduled run retries.
                continue
            for raw in raw_articles:
                article = _normalize(ticker, raw, provider="finnhub")
                if store.has_seen(article.article_id):
                    continue
                results.append(article)
                store.mark_seen(article.article_id)

    return results


async def _main() -> None:
    articles = await fetch_news()
    print(f"Fetched {len(articles)} new article(s):")
    for a in articles:
        print(f"  [{a.ticker}] {a.headline!r} — {a.source} ({a.published_at.isoformat()})")


if __name__ == "__main__":
    asyncio.run(_main())
