"""Standalone general-market news discovery for the britney.ai trading
agent — the "non-obvious" counterpart to agent/news_ingestion.py's
fixed-ticker company news. See docs/DISCOVERY_DESIGN.md for the design
investigation this implements (§1 general news source, §2 extraction +
validation).

Scope of this module: fetch Finnhub's general-category news, extract
company mentions via GPT-4o-mini (same OpenAI client pattern as
agent/sentiment.py's score_batch()), and hard-validate each extraction
against real market data (app/services/stock_data.py's Yahoo-backed
quote()) before it's eligible for anything downstream. Deliberately does
NOT persist anything, score sentiment, or trade — see agent/run_discovery.py
and app/services/discovery_service.py for where fetch/extract/validate
results actually get written to the discovered_candidates table (kept
out of this module so it stays standalone / DB-free, same discipline
news_ingestion.py's own docstring documents for itself).

Standalone — deliberately doesn't import app.database/app.models, so it
can be exercised without booting FastAPI or a real database:

    cd backend && python -m agent.discovery
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from openai import OpenAI, RateLimitError
from starlette.concurrency import run_in_threadpool

from agent import agent_config, news_ingestion
from agent.news_ingestion import (
    FINNHUB_BASE_URL,
    NewsArticle,
    RateLimiter,
    SeenArticleStore,
    _normalize,
)
from agent.sentiment import _parse_json_array, _retry_after_seconds
from app.config import get_settings
from app.services import stock_data

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"

SYSTEM = """You are a company-mention extractor for britney.ai, an investing assistant.
You will be given a numbered list of general market-news articles, each with a headline
and summary. For each article, identify the SINGLE most prominent publicly-traded company
mentioned, if any, and guess its stock ticker.
Respond ONLY with a valid JSON array, one object per article, in the SAME ORDER given:
[
  {"ticker_guess": "AAPL" | null, "company_name": "Apple Inc." | null, "confidence": number (0.0-1.0)}
]
Rules:
- If the article is not primarily about one identifiable publicly-traded company (e.g. it's
  macro/Fed/broad-market/private-company/no-company news), return ticker_guess: null,
  company_name: null, confidence: 0.0 for that article.
- ticker_guess must be your best real-world guess at the actual exchange ticker symbol for
  the company you named — never invent a symbol you are not reasonably confident is real.
- Be conservative with confidence: use a low score (< 0.5) when the company is only
  mentioned in passing, or when you are not confident of the exact ticker.
"""

# Rate-limit (429) retry policy — identical shape to agent/sentiment.py's
# own constants (exponential backoff honoring Retry-After when present).
# Kept as separate constants rather than importing sentiment.py's rather
# than duplicating: this module's retry loop is its own copy of
# sentiment.py's loop (see extract_companies_batch()), so its backoff
# knobs are declared alongside it for the same reason sentiment.py
# declares its own instead of importing from a shared constants module.
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_DELAY = 1.0
RATE_LIMIT_MAX_DELAY = 30.0

# Every ticker (stock/ETF AND crypto) already in the fixed target
# portfolios — a "discovery" that rediscovers VOO isn't discovery (gate
# 'c' in validate_candidate()). Derived from TARGET_PORTFOLIOS rather
# than duplicated by hand so this can never drift out of sync with it.
_FIXED_TARGET_TICKERS: set[str] = {
    symbol
    for assets in news_ingestion.TARGET_PORTFOLIOS.values()
    for symbol, _asset_type in assets
}

# Local sqlite state, same directory/gitignore convention as
# news_ingestion.py's SeenArticleStore (agent/.news_seen.sqlite3) — kept
# as separate files from that store rather than sharing it, since this
# pipeline's article-id namespace ("finnhub-general:<id>") and cursor
# state are specific to general news, not per-ticker company news.
_STATE_DIR = Path(__file__).parent
DISCOVERY_SEEN_DB_PATH = _STATE_DIR / ".discovery_seen.sqlite3"
DISCOVERY_CURSOR_DB_PATH = _STATE_DIR / ".discovery_cursor.sqlite3"


@dataclass
class ExtractionResult:
    article_id: str
    ticker_guess: str | None
    company_name: str | None
    confidence: float


@dataclass
class ValidationResult:
    ticker: str
    claimed_company_name: str
    passed: bool
    rejected_gate: str | None  # "a" | "b" | "c" | None (None == passed all three)
    reason: str
    quote_company_name: str | None = None


class MinIdCursorStore:
    """Tiny sqlite-backed single-value cursor: the highest Finnhub
    general-news article id that has been *fully processed* so far
    (extracted, validated, and marked seen in SeenArticleStore for every
    article up to that id) — NOT simply the highest id ever fetched.

    fetch_general_news() itself never writes this cursor; it only reads
    the value the caller passes in and reports back the highest raw id
    it observed. Advancing the persisted cursor is the caller's job
    (see agent/run_discovery.py), and deliberately only happens once
    every article in a fetched batch has been marked seen. If it
    advanced eagerly on fetch instead, a crash mid-batch (extraction
    error, DB write failure, process kill) would move minId past
    articles that were fetched but never finished processing — Finnhub
    would then never return them again, silently and permanently losing
    them. SeenArticleStore's own docstring documents the identical
    failure mode for article-level marking; this is the same discipline
    applied to the minId cursor.

    Kept as its own tiny store rather than folded into SeenArticleStore
    (article_id -> fetched_at, a growing set) because it's a different
    shape of state (one scalar) serving a different purpose: minId is
    purely a server-side payload-size optimization. SeenArticleStore
    alone already fully guarantees dedup correctness with no cursor at
    all — news_ingestion.fetch_news() proves this today, since it has no
    cursor concept and still dedupes correctly."""

    def __init__(self, db_path: str | Path | None = None):
        self._path = str(db_path or DISCOVERY_CURSOR_DB_PATH)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cursor (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                min_id INTEGER NOT NULL
            )
            """
        )
        self._conn.commit()

    def get(self) -> int:
        row = self._conn.execute("SELECT min_id FROM cursor WHERE id = 1").fetchone()
        return row[0] if row else 0

    def set(self, min_id: int) -> None:
        self._conn.execute(
            "INSERT INTO cursor (id, min_id) VALUES (1, ?) "
            "ON CONFLICT(id) DO UPDATE SET min_id = excluded.min_id",
            (min_id,),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()


def _mock_general_articles() -> list[dict]:
    """Deterministic offline fallback — same spirit as
    news_ingestion._mock_articles(), but general-market flavored (no
    single driving ticker) and with one headline deliberately
    company-shaped so mock-mode extraction (_mock_extraction() below)
    has something to find."""
    now = datetime.now(timezone.utc)
    return [
        {
            "id": "mock-general-1",
            "headline": "Fed holds rates steady, broader market shrugs",
            "summary": (
                "[Offline mode — add FINNHUB_API_KEY for real news] Mock "
                "general-market placeholder article, no single company."
            ),
            "source": "mock",
            "url": "",
            "datetime": int(now.timestamp()),
        },
        {
            "id": "mock-general-2",
            "headline": "Nvidia shares climb after analysts raise price targets",
            "summary": (
                "[Offline mode — add FINNHUB_API_KEY for real news] Second mock "
                "general-market placeholder article, deliberately company-shaped "
                "for local extraction testing."
            ),
            "source": "mock",
            "url": "",
            "datetime": int((now - timedelta(hours=3)).timestamp()),
        },
    ]


# Deterministic mock-mode extraction dictionary — same offline-mode
# discipline as sentiment.py's _mock_score() keyword heuristic, just for
# company extraction instead of sentiment. Not a real NER model; a
# best-effort placeholder so this pipeline is exercisable (and its
# control flow unit-testable) with no OPENAI_API_KEY configured.
_MOCK_COMPANY_KEYWORDS: dict[str, tuple[str, str]] = {
    "nvidia": ("NVDA", "NVIDIA Corporation"),
    "tesla": ("TSLA", "Tesla, Inc."),
    "amazon": ("AMZN", "Amazon.com, Inc."),
    "netflix": ("NFLX", "Netflix, Inc."),
    "meta": ("META", "Meta Platforms, Inc."),
}


def _mock_extraction(article: NewsArticle) -> ExtractionResult:
    text = f"{article.headline} {article.summary}".lower()
    for keyword, (ticker, company_name) in _MOCK_COMPANY_KEYWORDS.items():
        if keyword in text:
            return ExtractionResult(
                article_id=article.article_id,
                ticker_guess=ticker,
                company_name=company_name,
                confidence=0.6,
            )
    return ExtractionResult(
        article_id=article.article_id,
        ticker_guess=None,
        company_name=None,
        confidence=0.0,
    )


async def fetch_general_news(
    min_id: int = 0,
    *,
    store: SeenArticleStore | None = None,
    calls_per_minute: int = news_ingestion.DEFAULT_CALLS_PER_MINUTE,
) -> tuple[list[NewsArticle], int]:
    """Fetch, normalize, and dedupe Finnhub general-category news newer
    than `min_id`. Returns (unseen articles, highest raw Finnhub article
    id observed in this response) — see MinIdCursorStore's docstring for
    why persisting that id is the caller's job, not this function's.

    Reuses news_ingestion.NewsArticle/_normalize() rather than a second
    article shape — general-news articles are normalized with ticker=""
    (an empty string, not a per-ticker value) since a general-category
    article isn't about one company by definition; extract_companies_batch()
    below is what determines company mentions.

    Same mock-fallback/logging conventions as news_ingestion.fetch_news():
    works with no FINNHUB_API_KEY set, using deterministic offline
    placeholder articles instead. Does NOT mark returned articles seen
    itself (same discipline as fetch_news()) — the caller does that only
    once each article is fully handled.
    """
    settings = get_settings()
    store = store or SeenArticleStore(db_path=DISCOVERY_SEEN_DB_PATH)
    results: list[NewsArticle] = []

    if not settings.finnhub_api_key:
        logger.warning(
            "MOCK MODE: FINNHUB_API_KEY not set — returning offline placeholder "
            "general-news articles instead of calling the real Finnhub API."
        )
        for raw in _mock_general_articles():
            article = _normalize("", raw, provider="mock-general")
            if store.has_seen(article.article_id):
                continue
            results.append(article)
        return results, min_id

    logger.info("FINNHUB_API_KEY loaded — calling the real Finnhub general-news API.")
    limiter = RateLimiter(calls_per_minute)
    await limiter.acquire()

    max_id_seen = min_id
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{FINNHUB_BASE_URL}/news",
            params={"category": "general", "minId": min_id, "token": settings.finnhub_api_key},
            timeout=10.0,
        )
        logger.info("finnhub general-news request: minId=%s status=%s", min_id, resp.status_code)
        resp.raise_for_status()
        raw_articles = resp.json()

    if not isinstance(raw_articles, list):
        raw_articles = []

    new_count = 0
    for raw in raw_articles:
        raw_id = raw.get("id")
        if isinstance(raw_id, int):
            max_id_seen = max(max_id_seen, raw_id)
        article = _normalize("", raw, provider="finnhub-general")
        if store.has_seen(article.article_id):
            continue
        results.append(article)
        new_count += 1

    logger.info(
        "general-news: %d fetched, %d new (%d already seen), max_id=%d",
        len(raw_articles), new_count, len(raw_articles) - new_count, max_id_seen,
    )
    return results, max_id_seen


def _coerce_extraction(article: NewsArticle, raw: dict[str, Any]) -> ExtractionResult:
    ticker_guess = raw.get("ticker_guess")
    if not isinstance(ticker_guess, str) or not ticker_guess.strip():
        ticker_guess = None
    else:
        ticker_guess = ticker_guess.strip().upper()

    company_name = raw.get("company_name")
    if not isinstance(company_name, str) or not company_name.strip():
        company_name = None

    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    return ExtractionResult(
        article_id=article.article_id,
        ticker_guess=ticker_guess,
        company_name=company_name,
        confidence=confidence,
    )


async def extract_companies_batch(
    articles: list[NewsArticle],
    *,
    batch_size: int = agent_config.DISCOVERY_EXTRACTION_BATCH_SIZE,
) -> list[ExtractionResult]:
    """Extract at most one company mention per article via gpt-4o-mini,
    batching `batch_size` per call — same shape as
    agent/sentiment.py's score_batch(): same OpenAI(max_retries=0)
    client, same run_in_threadpool offload (the OpenAI SDK's default
    client is sync/blocking, and this runs on the shared asyncio event
    loop), same 429 backoff (reusing sentiment._retry_after_seconds()),
    same JSON-array parsing (reusing sentiment._parse_json_array()), and
    the same per-chunk mock fallback on any other error. Result order
    matches `articles`.

    IMPORTANT: this function's output (`ticker_guess`) is never trade-
    eligible on its own — see validate_candidate() below. This function
    only proposes candidates; it does not validate them against real
    market data.
    """
    if not articles:
        return []

    settings = get_settings()
    if not settings.openai_api_key:
        logger.warning(
            "mock fallback (no API key) — extracting %d article(s) with the "
            "deterministic offline heuristic.",
            len(articles),
        )
        return [_mock_extraction(a) for a in articles]

    # max_retries=0: same reasoning as sentiment.py's score_batch() — we
    # own retry behavior below (429s get a deliberate backoff schedule;
    # everything else falls back to mock), rather than the SDK silently
    # retrying some errors on its own schedule first.
    client = OpenAI(api_key=settings.openai_api_key, max_retries=0)
    results: list[ExtractionResult] = []

    for i in range(0, len(articles), batch_size):
        chunk = articles[i : i + batch_size]
        user_prompt = "Extract company mentions from these articles, in order:\n\n" + "\n\n".join(
            f"{idx + 1}. Headline: {a.headline}\n"
            f"Summary: {a.summary or '(no summary)'}"
            for idx, a in enumerate(chunk)
        )
        rate_limit_attempt = 0
        while True:
            try:
                resp = await run_in_threadpool(
                    client.chat.completions.create,
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.1,
                )
                raw = resp.choices[0].message.content or "[]"
                parsed = _parse_json_array(raw)
                if len(parsed) != len(chunk):
                    raise ValueError(
                        f"Expected {len(chunk)} extraction objects, got {len(parsed)}"
                    )
                results.extend(_coerce_extraction(a, r) for a, r in zip(chunk, parsed))
                logger.info(
                    "real LLM success — extracted %d article(s) [%d:%d] via %s.",
                    len(chunk), i, i + len(chunk), MODEL,
                )
                break
            except RateLimitError as exc:
                rate_limit_attempt += 1
                if rate_limit_attempt > RATE_LIMIT_MAX_RETRIES:
                    logger.error(
                        "mock fallback (API error: %s status=%s: %s) — rate-limit "
                        "retries exhausted (%d/%d) extracting %d article(s) [%d:%d] "
                        "with the offline heuristic instead of %s.",
                        type(exc).__name__, exc.status_code, exc,
                        RATE_LIMIT_MAX_RETRIES, RATE_LIMIT_MAX_RETRIES,
                        len(chunk), i, i + len(chunk), MODEL,
                    )
                    results.extend(_mock_extraction(a) for a in chunk)
                    break
                delay = _retry_after_seconds(exc)
                if delay is None:
                    delay = min(
                        RATE_LIMIT_BASE_DELAY * (2 ** (rate_limit_attempt - 1)),
                        RATE_LIMIT_MAX_DELAY,
                    )
                else:
                    delay = min(delay, RATE_LIMIT_MAX_DELAY)
                logger.warning(
                    "rate limited, retrying (attempt %d/%d, waiting %.1fs) — "
                    "batch [%d:%d].",
                    rate_limit_attempt, RATE_LIMIT_MAX_RETRIES, delay, i, i + len(chunk),
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                status_code = getattr(exc, "status_code", None)
                status_part = f" status={status_code}" if status_code is not None else ""
                logger.error(
                    "mock fallback (API error: %s%s: %s) — extracting %d article(s) "
                    "[%d:%d] with the offline heuristic instead of %s.",
                    type(exc).__name__, status_part, exc,
                    len(chunk), i, i + len(chunk), MODEL,
                )
                results.extend(_mock_extraction(a) for a in chunk)
                break

    return results


# Tokens dropped when normalizing a company name for fuzzy matching —
# corporate suffixes and filler words that would otherwise make
# "Apple Inc." and "Apple Corp" look less similar than they are.
_SUFFIX_TOKENS = {
    "inc", "incorporated", "corp", "corporation", "co", "company",
    "ltd", "limited", "plc", "llc", "the", "group", "holdings",
    "class", "a", "b",
}


def _normalize_company_name(name: str) -> set[str]:
    """Lowercase, strip punctuation, and drop common corporate-suffix/
    filler tokens so "Apple Inc." and "apple" tokenize to the same
    {"apple"} set."""
    cleaned = re.sub(r"[^\w\s]", " ", name.lower())
    return {t for t in cleaned.split() if t and t not in _SUFFIX_TOKENS}


def company_name_matches(quote_name: str, claimed_name: str, *, threshold: float = 0.5) -> bool:
    """True if `quote_name` (from stock_data.quote(), the ground truth)
    and `claimed_name` (the LLM's extraction) overlap enough to
    plausibly be the same company.

    Deliberately not a fuzzy-match library (rapidfuzz/thefuzz aren't in
    requirements.txt) — a single company-name comparison in one call
    site doesn't justify a new dependency, and normalized-token overlap
    is easy to reason about and unit test deterministically (no external
    edit-distance implementation to trust). Ratio is intersection size
    over the SHORTER token set (not union/Jaccard), so a short claimed
    name like "Apple" still matches the longer official "Apple Inc." —
    the overlap only needs to cover the smaller side, not both.

    Strictly-greater-than `threshold` (not >=): with the default 0.5,
    two two-token names sharing exactly one token (e.g. "Meta Materials
    Inc." vs. "Meta Platforms" both tokenizing to a 2-token set sharing
    only {"meta"}, ratio == 0.5 exactly) must be REJECTED, not passed —
    that's precisely the "LLM named a real company but the wrong one"
    failure mode this gate exists to catch, and it was caught by a real
    test case (tests/test_discovery.py) hitting exactly this ratio
    before this was changed from `>=` to `>`.
    """
    quote_tokens = _normalize_company_name(quote_name)
    claimed_tokens = _normalize_company_name(claimed_name)
    if not quote_tokens or not claimed_tokens:
        return False
    overlap = len(quote_tokens & claimed_tokens)
    smaller = min(len(quote_tokens), len(claimed_tokens))
    return (overlap / smaller) > threshold


async def validate_candidate(
    ticker_guess: str, claimed_company_name: str | None
) -> ValidationResult:
    """Hard validation gate — see docs/DISCOVERY_DESIGN.md §2. A
    candidate is only ever eligible for persistence (and, in later
    prompts, for anything trade-related) if it clears all three gates
    below, checked IN ORDER with short-circuit on first failure:

      (a) stock_data.quote(ticker) returns a real, non-None quote.
      (b) the quote's company_name fuzzy-matches the LLM's claimed
          company_name — catches the LLM naming a real company but
          guessing the WRONG ticker for it (quote() alone would happily
          return a valid quote for the wrong company in that case).
      (c) the resolved ticker isn't already one of the fixed
          TARGET_PORTFOLIOS tickers — rediscovering VOO isn't discovery.

    An LLM-hallucinated or mismatched ticker reaching anything
    trade-related unvalidated would be a real correctness bug in a
    trading system, not cosmetic noise — this function is the
    non-negotiable fix for that, and every rejection is logged with
    which gate rejected it and why, for debuggability.
    """
    ticker = ticker_guess.strip().upper()
    claimed_name = (claimed_company_name or "").strip()

    quote = await stock_data.quote(ticker)
    if quote is None:
        result = ValidationResult(
            ticker=ticker,
            claimed_company_name=claimed_name,
            passed=False,
            rejected_gate="a",
            reason=(
                f"stock_data.quote({ticker!r}) returned no quote — either not a "
                f"real/tradable ticker, or a transient fetch failure (see quote()'s "
                f"own ERROR/INFO-level logging above for which)."
            ),
        )
        logger.info("validation gate 'a' REJECTED %s: %s", ticker, result.reason)
        return result

    quote_name = str(quote.get("company_name") or "")
    if not company_name_matches(quote_name, claimed_name):
        result = ValidationResult(
            ticker=ticker,
            claimed_company_name=claimed_name,
            passed=False,
            rejected_gate="b",
            reason=(
                f"claimed company_name {claimed_name!r} does not match quote's "
                f"company_name {quote_name!r} for {ticker} — likely the LLM naming "
                f"a real company but guessing the wrong ticker for it."
            ),
            quote_company_name=quote_name,
        )
        logger.info("validation gate 'b' REJECTED %s: %s", ticker, result.reason)
        return result

    if ticker in _FIXED_TARGET_TICKERS:
        result = ValidationResult(
            ticker=ticker,
            claimed_company_name=claimed_name,
            passed=False,
            rejected_gate="c",
            reason=f"{ticker} is already a fixed TARGET_PORTFOLIOS ticker — not discovery.",
            quote_company_name=quote_name,
        )
        logger.info("validation gate 'c' REJECTED %s: %s", ticker, result.reason)
        return result

    result = ValidationResult(
        ticker=ticker,
        claimed_company_name=claimed_name,
        passed=True,
        rejected_gate=None,
        reason="passed all three validation gates",
        quote_company_name=quote_name,
    )
    logger.info("validation PASSED %s (%s)", ticker, quote_name)
    return result


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # Same reasoning as news_ingestion.py's _main(): httpx logs the full
    # request URL (including ?token=<FINNHUB_API_KEY>) at INFO level.
    logging.getLogger("httpx").setLevel(logging.WARNING)

    cursor_store = MinIdCursorStore()
    seen_store = SeenArticleStore(db_path=DISCOVERY_SEEN_DB_PATH)
    articles, max_id = await fetch_general_news(cursor_store.get(), store=seen_store)
    print(f"Fetched {len(articles)} unprocessed general-news article(s), max_id={max_id}:")
    for a in articles:
        print(f"  {a.headline!r} — {a.source} ({a.published_at.isoformat()})")

    if not articles:
        return

    extractions = await extract_companies_batch(articles)
    print("\nExtractions:")
    for article, extraction in zip(articles, extractions):
        print(
            f"  [{extraction.ticker_guess}] {extraction.company_name!r} "
            f"conf={extraction.confidence:.2f} <- {article.headline!r}"
        )


if __name__ == "__main__":
    asyncio.run(_main())
