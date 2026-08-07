"""
Standalone news ingestion for the britney.ai trading agent.

Pulls company news from Finnhub's free tier for the stock/ETF tickers in
the three target portfolios (conservative/moderate/aggressive), and crypto
news from CryptoPanic's free tier for the crypto tickers in those same
portfolios — Finnhub's free /company-news endpoint doesn't cover crypto,
so it needs a second, separate provider (see fetch_news() vs.
fetch_crypto_news() below). Both throttle calls via the same RateLimiter,
normalize into the exact same NewsArticle shape, and dedupe articles
already delivered in a prior run via the same small local sqlite cache.

Standalone — deliberately doesn't import app.database/app.models, so it
can be exercised without booting FastAPI or a real database:

    cd backend && python -m agent.news_ingestion
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from app.config import get_settings

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
CRYPTOPANIC_BASE_URL = "https://cryptopanic.com/api/v1"

logger = logging.getLogger(__name__)

# Finnhub's free tier allows ~60 calls/min; stay under that with headroom
# in case something else in the process is also calling Finnhub. Reused
# as-is for CryptoPanic too (see fetch_crypto_news()) — both providers'
# free tiers are comparably rate-limited, and this is a conservative
# shared default rather than something either provider requires exactly.
DEFAULT_CALLS_PER_MINUTE = 50

# Mirrors the three risk buckets in app/ai/recommendation_engine.py's mock
# fallback (conservative == low risk, moderate == medium, aggressive ==
# high). Stock/ETF tickers get news from fetch_news() (Finnhub); crypto
# tickers get news from fetch_crypto_news() (CryptoPanic) — Finnhub's free
# company-news endpoint only covers equities/ETFs.
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


def target_stock_tickers_for(tier: str) -> list[str]:
    """Stock/ETF tickers for a single risk tier ("conservative" |
    "moderate" | "aggressive") — crypto entries in that tier are dropped
    for the same reason as target_stock_tickers()."""
    return [symbol for symbol, asset_type in TARGET_PORTFOLIOS[tier] if asset_type == "stock"]


def target_crypto_tickers() -> list[str]:
    """Unique, order-stable crypto tickers across all three target
    portfolios — the ones fetch_crypto_news() (CryptoPanic) covers,
    symmetric to target_stock_tickers()."""
    seen: set[str] = set()
    tickers: list[str] = []
    for assets in TARGET_PORTFOLIOS.values():
        for symbol, asset_type in assets:
            if asset_type != "crypto" or symbol in seen:
                continue
            seen.add(symbol)
            tickers.append(symbol)
    return tickers


def target_crypto_tickers_for(tier: str) -> list[str]:
    """Crypto tickers for a single risk tier — stock/ETF entries in that
    tier are dropped, symmetric to target_stock_tickers_for()."""
    return [symbol for symbol, asset_type in TARGET_PORTFOLIOS[tier] if asset_type == "crypto"]


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
    """Tiny sqlite-backed dedupe cache: article_id -> first-processed
    timestamp. Kept separate from the main app DB on purpose (see module
    docstring) — gitignored like every other *.sqlite3 file in this repo.

    IMPORTANT: fetch_news() does NOT call mark_seen() itself — it only
    reads has_seen() to filter out already-processed articles. Marking is
    the caller's job, and should only happen once an article has been
    fully handled (e.g. decision_loop.py calls mark_seen() only after
    create_agent_decision() successfully commits for that article's
    ticker). This is deliberate: if fetch_news() marked articles seen at
    fetch time, a crash/error anywhere downstream (LLM call, DB write)
    would silently and permanently lose that article — it would never be
    fetched again since it's already "seen"."""

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


def _mock_crypto_articles(ticker: str) -> list[dict]:
    """Crypto analog of _mock_articles() — same shape, same offline-mode
    discipline, just crypto-flavored copy so mock CryptoPanic runs don't
    read like recycled equity headlines."""
    now = datetime.now(timezone.utc)
    return [
        {
            "id": f"{ticker}-mock-1",
            "headline": f"{ticker} trades sideways as market awaits next catalyst",
            "summary": (
                "[Offline mode — add CRYPTOPANIC_API_KEY for real news] Mock "
                f"placeholder article for {ticker}."
            ),
            "source": "mock",
            "url": "",
            "datetime": int(now.timestamp()),
        },
        {
            "id": f"{ticker}-mock-2",
            "headline": f"Analysts debate {ticker}'s next move",
            "summary": (
                "[Offline mode — add CRYPTOPANIC_API_KEY for real news] Second "
                f"mock placeholder article for {ticker}."
            ),
            "source": "mock",
            "url": "",
            "datetime": int((now - timedelta(hours=6)).timestamp()),
        },
    ]


def _cryptopanic_post_to_raw(post: dict) -> dict:
    """Reshapes one CryptoPanic /posts/ result into the same intermediate
    dict shape Finnhub articles already use (id/headline/summary/source/
    url/datetime-as-epoch-int), so it can go through the exact same
    _normalize() below instead of a second normalization path. CryptoPanic's
    free tier doesn't include full article bodies, only a title — used for
    both headline and summary, same as sentiment.py already tolerates for
    any article with no distinct summary (`a.summary or '(no summary)'`)."""
    published_at = post.get("published_at") or post.get("created_at")
    ts: int | None = None
    if published_at:
        try:
            ts = int(datetime.fromisoformat(published_at.replace("Z", "+00:00")).timestamp())
        except ValueError:
            ts = None
    title = post.get("title", "")
    return {
        "id": post.get("id"),
        "headline": title,
        "summary": title,
        "source": (post.get("source") or {}).get("title") or "cryptopanic",
        "url": post.get("url", ""),
        "datetime": ts,
    }


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
    logger.info("finnhub company-news request: %s status=%s", ticker, resp.status_code)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


async def _fetch_ticker_news_cryptopanic(
    client: httpx.AsyncClient,
    ticker: str,
    api_key: str,
    limiter: RateLimiter,
) -> list[dict]:
    await limiter.acquire()
    resp = await client.get(
        f"{CRYPTOPANIC_BASE_URL}/posts/",
        params={"auth_token": api_key, "currencies": ticker, "public": "true"},
        timeout=10.0,
    )
    logger.info("cryptopanic posts request: %s status=%s", ticker, resp.status_code)
    resp.raise_for_status()
    data = resp.json()
    return data.get("results", []) if isinstance(data, dict) else []


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
    be exercised offline. Articles already marked processed in a prior
    call (tracked in `store`, persisted across runs) are skipped.

    Does NOT mark the articles it returns as seen/processed — see
    SeenArticleStore's docstring for why. Call `store.mark_seen(article_id)`
    yourself once each article has been fully handled by your pipeline.
    """
    settings = get_settings()
    tickers = tickers or target_stock_tickers()
    store = store or SeenArticleStore()
    results: list[NewsArticle] = []

    if not settings.finnhub_api_key:
        logger.warning(
            "MOCK MODE: FINNHUB_API_KEY not set — returning offline placeholder "
            "articles instead of calling the real Finnhub API."
        )
        for ticker in tickers:
            for raw in _mock_articles(ticker):
                article = _normalize(ticker, raw, provider="mock")
                if store.has_seen(article.article_id):
                    continue
                results.append(article)
        return results

    logger.info("FINNHUB_API_KEY loaded — calling the real Finnhub API.")
    limiter = RateLimiter(calls_per_minute)
    failed_tickers: list[str] = []
    async with httpx.AsyncClient() as client:
        for ticker in tickers:
            try:
                raw_articles = await _fetch_ticker_news_finnhub(
                    client, ticker, settings.finnhub_api_key, limiter, days_back
                )
            except httpx.HTTPError as exc:
                # Skip this ticker for this run; the next scheduled run retries.
                failed_tickers.append(ticker)
                logger.error("REAL API CALL FAILED for %s: %s", ticker, exc)
                continue
            new_count = 0
            for raw in raw_articles:
                article = _normalize(ticker, raw, provider="finnhub")
                if store.has_seen(article.article_id):
                    continue
                results.append(article)
                new_count += 1
            logger.info(
                "%s: %d fetched, %d new (%d already seen)",
                ticker, len(raw_articles), new_count, len(raw_articles) - new_count,
            )

    if failed_tickers:
        logger.warning(
            "Real Finnhub API call failed for %d/%d ticker(s): %s",
            len(failed_tickers), len(tickers), ", ".join(failed_tickers),
        )
    elif not results:
        logger.info(
            "REAL API CALL SUCCEEDED, ZERO NEW ARTICLES: Finnhub returned no "
            "new (unseen) articles for any of %d ticker(s) in the last %d day(s).",
            len(tickers), days_back,
        )

    return results


async def fetch_crypto_news(
    tickers: list[str] | None = None,
    *,
    store: SeenArticleStore | None = None,
    calls_per_minute: int = DEFAULT_CALLS_PER_MINUTE,
) -> list[NewsArticle]:
    """Crypto analog of fetch_news() — same NewsArticle shape, same
    SeenArticleStore dedupe discipline (does NOT mark articles seen
    itself, see SeenArticleStore's docstring), same mock-fallback/logging
    pattern, same RateLimiter — sourced from CryptoPanic instead of
    Finnhub, since Finnhub's free /company-news endpoint doesn't cover
    crypto. Kept as a separate function rather than merged into
    fetch_news() so each provider's request shape and error handling stay
    independent; callers that want both call each function separately and
    combine the results (see _main() below for the standalone example).
    """
    settings = get_settings()
    tickers = tickers or target_crypto_tickers()
    store = store or SeenArticleStore()
    results: list[NewsArticle] = []

    if not settings.cryptopanic_api_key:
        logger.warning(
            "MOCK MODE: CRYPTOPANIC_API_KEY not set — returning offline placeholder "
            "articles instead of calling the real CryptoPanic API."
        )
        for ticker in tickers:
            for raw in _mock_crypto_articles(ticker):
                article = _normalize(ticker, raw, provider="mock")
                if store.has_seen(article.article_id):
                    continue
                results.append(article)
        return results

    logger.info("CRYPTOPANIC_API_KEY loaded — calling the real CryptoPanic API.")
    limiter = RateLimiter(calls_per_minute)
    failed_tickers: list[str] = []
    async with httpx.AsyncClient() as client:
        for ticker in tickers:
            try:
                raw_posts = await _fetch_ticker_news_cryptopanic(
                    client, ticker, settings.cryptopanic_api_key, limiter
                )
            except httpx.HTTPError as exc:
                # Skip this ticker for this run; the next scheduled run retries.
                failed_tickers.append(ticker)
                logger.error("REAL API CALL FAILED for %s: %s", ticker, exc)
                continue
            new_count = 0
            for post in raw_posts:
                article = _normalize(ticker, _cryptopanic_post_to_raw(post), provider="cryptopanic")
                if store.has_seen(article.article_id):
                    continue
                results.append(article)
                new_count += 1
            logger.info(
                "%s: %d fetched, %d new (%d already seen)",
                ticker, len(raw_posts), new_count, len(raw_posts) - new_count,
            )

    if failed_tickers:
        logger.warning(
            "Real CryptoPanic API call failed for %d/%d ticker(s): %s",
            len(failed_tickers), len(tickers), ", ".join(failed_tickers),
        )
    elif not results:
        logger.info(
            "REAL API CALL SUCCEEDED, ZERO NEW ARTICLES: CryptoPanic returned no "
            "new (unseen) posts for any of %d ticker(s).",
            len(tickers),
        )

    return results


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs the full request URL at INFO level, which includes
    # ?token=<FINNHUB_API_KEY> / ?auth_token=<CRYPTOPANIC_API_KEY> in
    # plaintext — drop it to WARNING so neither key ever lands in logs. Our
    # own "finnhub company-news request"/"cryptopanic posts request" log
    # lines already report ticker + status code without the token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Standalone smoke-test entry point — nothing downstream consumes these
    # articles, so intentionally does NOT mark them processed (see
    # SeenArticleStore's docstring). Running this script is safe to repeat
    # and won't steal articles from decision_loop.py's real pipeline. Calls
    # both providers separately (same pattern decision_loop.py would use to
    # combine them) rather than merging into one fetch call.
    stock_articles = await fetch_news()
    crypto_articles = await fetch_crypto_news()
    articles = stock_articles + crypto_articles
    print(
        f"Fetched {len(articles)} unprocessed article(s) "
        f"({len(stock_articles)} stock/ETF via Finnhub, "
        f"{len(crypto_articles)} crypto via CryptoPanic):"
    )
    for a in articles:
        print(f"  [{a.ticker}] {a.headline!r} — {a.source} ({a.published_at.isoformat()})")


if __name__ == "__main__":
    asyncio.run(_main())
