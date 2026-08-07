"""
Standalone sentiment scoring for the britney.ai trading agent.

Scores news articles (see agent/news_ingestion.py) bullish/bearish/neutral
using the same OpenAI (gpt-4o-mini) client setup and mock-fallback pattern
as app/ai/recommendation_engine.py, so this works with no OPENAI_API_KEY
set — just like the rest of the app's AI features.

Standalone — doesn't import app.database/app.models:

    cd backend && python -m agent.sentiment
"""

from __future__ import annotations

import asyncio
import email.utils
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from openai import OpenAI, RateLimitError
from starlette.concurrency import run_in_threadpool

from agent.news_ingestion import NewsArticle, fetch_news
from app.config import get_settings

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"

SYSTEM = """You are a financial news sentiment scorer for britney.ai, an investing assistant.
You will be given a numbered list of articles, each with a ticker, headline, and summary.
For each article, decide whether it reads as bullish, bearish, or neutral for that ticker,
and how confident you are.
Respond ONLY with a valid JSON array, one object per article, in the SAME ORDER given:
[
  {"sentiment": "bullish" | "bearish" | "neutral", "confidence": number (0.0-1.0), "reasoning": "one sentence"}
]
Be conservative — use "neutral" with lower confidence when an article is ambiguous, purely
factual with no clear directional read, or not actually about the company's fundamentals.
"""

# Batch size cap: keeps each request's prompt manageable and bounds how much
# of a batch is lost/retried if one call's response fails to parse.
DEFAULT_BATCH_SIZE = 10

# Rate-limit (429) retry policy: exponential backoff (1s, 2s, 4s, ...),
# capped at RATE_LIMIT_MAX_DELAY, honoring the response's Retry-After
# header when present instead of guessing. Only 429s retry — other
# exceptions (auth errors, malformed responses, etc.) fall back to mock
# immediately since retrying wouldn't help.
RATE_LIMIT_MAX_RETRIES = 5
RATE_LIMIT_BASE_DELAY = 1.0
RATE_LIMIT_MAX_DELAY = 30.0


@dataclass
class ScoreResult:
    article_id: str
    ticker: str
    sentiment: str  # bullish | bearish | neutral
    confidence: float
    reasoning: str


def _mock_score(article: NewsArticle) -> ScoreResult:
    """Deterministic offline fallback — same spirit as the OpenAI mocks in
    recommendation_engine.py/chat_engine.py/news_ingestion.py. A simple
    keyword heuristic, not a real sentiment model."""
    text = f"{article.headline} {article.summary}".lower()
    bullish_words = ("beat", "surge", "record", "upgrade", "rally", "steady", "growth")
    bearish_words = ("miss", "fall", "downgrade", "lawsuit", "plunge", "cut", "risk")
    if any(w in text for w in bullish_words):
        sentiment, confidence = "bullish", 0.55
    elif any(w in text for w in bearish_words):
        sentiment, confidence = "bearish", 0.55
    else:
        sentiment, confidence = "neutral", 0.4
    return ScoreResult(
        article_id=article.article_id,
        ticker=article.ticker,
        sentiment=sentiment,
        confidence=confidence,
        reasoning=(
            "[Offline mode — add OPENAI_API_KEY for real scoring] "
            f"Keyword heuristic on headline/summary for {article.ticker}."
        ),
    )


def _retry_after_seconds(exc: RateLimitError) -> float | None:
    """Seconds to wait before retrying, taken from the 429 response's
    Retry-After header (either a plain seconds value or an HTTP-date) —
    None if the header is absent or unparseable, so the caller falls back
    to its own exponential-backoff guess."""
    response = exc.response
    retry_after = response.headers.get("retry-after") if response is not None else None
    if retry_after is None:
        return None
    try:
        return max(0.0, float(retry_after))
    except (TypeError, ValueError):
        pass
    try:
        retry_date = email.utils.parsedate_to_datetime(retry_after)
    except (TypeError, ValueError):
        return None
    if retry_date is None:
        return None
    return max(0.0, (retry_date - datetime.now(retry_date.tzinfo)).total_seconds())


def _parse_json_array(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    m = re.search(r"\[[\s\S]*\]", text)
    if m:
        text = m.group(0)
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of score objects")
    return data


def _coerce_result(article: NewsArticle, raw: dict[str, Any]) -> ScoreResult:
    sentiment = raw.get("sentiment")
    if sentiment not in ("bullish", "bearish", "neutral"):
        sentiment = "neutral"
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return ScoreResult(
        article_id=article.article_id,
        ticker=article.ticker,
        sentiment=sentiment,
        confidence=confidence,
        reasoning=str(raw.get("reasoning", "")),
    )


async def score_article(article: NewsArticle) -> ScoreResult:
    """Score a single article. For more than a couple of articles, prefer
    score_batch() — it groups several articles into one LLM call."""
    results = await score_batch([article], batch_size=1)
    return results[0]


async def score_batch(
    articles: list[NewsArticle],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[ScoreResult]:
    """Score many articles, batching `batch_size` per LLM call to save
    tokens vs. one call per article. Result order matches `articles`."""
    if not articles:
        return []

    settings = get_settings()
    if not settings.openai_api_key:
        logger.warning(
            "mock fallback (no API key) — scoring %d article(s) with the offline heuristic.",
            len(articles),
        )
        return [_mock_score(a) for a in articles]

    # max_retries=0: we own retry behavior below (429s get a deliberate
    # backoff schedule; everything else fails straight to mock), rather
    # than the SDK silently retrying some errors on its own schedule first.
    client = OpenAI(api_key=settings.openai_api_key, max_retries=0)
    results: list[ScoreResult] = []

    for i in range(0, len(articles), batch_size):
        chunk = articles[i : i + batch_size]
        user_prompt = "Score these articles, in order:\n\n" + "\n\n".join(
            f"{idx + 1}. Ticker: {a.ticker}\n"
            f"Headline: {a.headline}\n"
            f"Summary: {a.summary or '(no summary)'}"
            for idx, a in enumerate(chunk)
        )
        rate_limit_attempt = 0
        while True:
            try:
                # The OpenAI SDK's default client is sync (blocking network
                # I/O) — this function is async and runs on the shared event
                # loop (called from app.ai.recommendation_engine's request
                # path), so a direct call here would stall every other
                # in-flight request for the duration of the API call.
                resp = await run_in_threadpool(
                    client.chat.completions.create,
                    model=MODEL,
                    messages=[
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": user_prompt},
                    ],
                    temperature=0.2,
                )
                raw = resp.choices[0].message.content or "[]"
                parsed = _parse_json_array(raw)
                if len(parsed) != len(chunk):
                    raise ValueError(
                        f"Expected {len(chunk)} score objects, got {len(parsed)}"
                    )
                results.extend(_coerce_result(a, r) for a, r in zip(chunk, parsed))
                logger.info(
                    "real LLM success — scored %d article(s) [%d:%d] via %s.",
                    len(chunk), i, i + len(chunk), MODEL,
                )
                break
            except RateLimitError as exc:
                rate_limit_attempt += 1
                if rate_limit_attempt > RATE_LIMIT_MAX_RETRIES:
                    logger.error(
                        "mock fallback (API error: %s status=%s: %s) — rate-limit "
                        "retries exhausted (%d/%d) scoring %d article(s) [%d:%d] "
                        "with the offline heuristic instead of %s.",
                        type(exc).__name__, exc.status_code, exc,
                        RATE_LIMIT_MAX_RETRIES, RATE_LIMIT_MAX_RETRIES,
                        len(chunk), i, i + len(chunk), MODEL,
                    )
                    results.extend(_mock_score(a) for a in chunk)
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
                # Fall back to the offline heuristic for just this chunk rather
                # than losing/crashing the whole batch on one bad response. Log
                # the original exception so billing/auth errors are visibly
                # distinct from an intentional mock run. Not a RateLimitError,
                # so no retry — retrying wouldn't help these.
                status_code = getattr(exc, "status_code", None)
                status_part = f" status={status_code}" if status_code is not None else ""
                logger.error(
                    "mock fallback (API error: %s%s: %s) — scoring %d article(s) [%d:%d] "
                    "with the offline heuristic instead of %s.",
                    type(exc).__name__, status_part, exc,
                    len(chunk), i, i + len(chunk), MODEL,
                )
                results.extend(_mock_score(a) for a in chunk)
                break

    return results


async def _main() -> None:
    # Standalone smoke-test entry point — scoring here doesn't persist
    # anywhere, so intentionally does NOT mark articles processed (see
    # agent.news_ingestion.SeenArticleStore's docstring). Only
    # decision_loop.py's real pipeline does that, and only after a
    # decision is durably recorded for the article.
    articles = await fetch_news()
    if not articles:
        print("No unprocessed articles to score (already processed, or none fetched).")
        return
    results = await score_batch(articles)
    for r in results:
        print(f"[{r.ticker}] {r.sentiment} ({r.confidence:.2f}) — {r.reasoning}")


if __name__ == "__main__":
    asyncio.run(_main())
