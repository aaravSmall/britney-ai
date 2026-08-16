"""Coverage for the news-dedup guarantee that controls OpenAI cost at
higher poll frequency (agent/news_ingestion.py's SeenArticleStore +
agent/decision_loop.py's run_once()) — an already-seen article must never
reach agent/sentiment.py's score_batch(), since that's the only thing
that makes a real (billed) OpenAI call. This wasn't covered by any
existing test file (checked) before the poll-interval speedup made it
load-bearing for cost control, not just article-processing correctness.

Uses news_ingestion's mock-mode fallback (_mock_articles/
_mock_crypto_articles, deterministic per-ticker ids) rather than a real
Finnhub/CryptoPanic call — exactly the same mechanism real crypto polling
already runs under in production (no CRYPTOPANIC_API_KEY configured), so
this doubles as evidence for "crypto re-polling is a no-op" at the unit
level, alongside the real droplet log evidence in the verification report.
"""

import asyncio
from dataclasses import dataclass

import pytest

from agent import news_ingestion
from agent.news_ingestion import SeenArticleStore


@dataclass
class _NoKeysSettings:
    """Forces news_ingestion's mock-mode fallback regardless of whatever
    real FINNHUB_API_KEY happens to be configured in this machine's local
    .env — these tests need the deterministic mock article ids, not a
    live network call (that live path is already exercised elsewhere,
    e.g. tests/test_stocks.py)."""

    finnhub_api_key: str = ""
    cryptopanic_api_key: str = ""


@pytest.fixture
def seen_store(tmp_path, monkeypatch):
    monkeypatch.setattr(news_ingestion, "get_settings", lambda: _NoKeysSettings())
    store = SeenArticleStore(db_path=tmp_path / "test_seen.sqlite3")
    yield store
    store.close()


def test_fetch_news_returns_nothing_once_articles_are_marked_seen(seen_store):
    """Mock mode (no FINNHUB_API_KEY — same fallback real crypto polling
    always uses, no CRYPTOPANIC_API_KEY ever configured) returns
    deterministic per-ticker article ids, so a second fetch against the
    same persistent store is the exact scenario a faster poll interval
    creates many more of: re-polling unchanged news."""
    first = asyncio.run(news_ingestion.fetch_news(tickers=["VOO"], store=seen_store))
    assert len(first) == 2  # _mock_articles() always returns 2 per ticker

    for article in first:
        seen_store.mark_seen(article.article_id)

    second = asyncio.run(news_ingestion.fetch_news(tickers=["VOO"], store=seen_store))
    assert second == []

    print(f"\nFirst fetch: {len(first)} article(s). After marking seen, second fetch: {len(second)}.")


def test_fetch_crypto_news_returns_nothing_once_articles_are_marked_seen(seen_store):
    """Same guarantee, crypto side — this is the literal mechanism behind
    'crypto re-polling is a no-op' (item 4): CRYPTOPANIC_API_KEY has never
    been configured, so every crypto poll already runs through this exact
    mock path in production, not just in this test."""
    first = asyncio.run(news_ingestion.fetch_crypto_news(tickers=["ETH"], store=seen_store))
    assert len(first) == 2

    for article in first:
        seen_store.mark_seen(article.article_id)

    second = asyncio.run(news_ingestion.fetch_crypto_news(tickers=["ETH"], store=seen_store))
    assert second == []


def test_run_once_never_calls_score_batch_when_nothing_new(client, monkeypatch):
    """decision_loop.run_once() only calls sentiment.score_batch() (the
    function that makes the real, billed OpenAI call) `if articles:` —
    with fetch_news()/fetch_crypto_news() already filtering out
    already-seen articles before run_once() ever sees them, an all-seen
    poll must short-circuit before scoring, not just before trading.
    Monkeypatches decision_loop's own imported references to
    fetch_news/fetch_crypto_news/score_batch directly (not the client
    fixture's app/DB) since this is testing run_once()'s control flow in
    isolation, not the full trade-execution path other test files cover."""
    import agent.decision_loop as decision_loop

    call_count = {"score_batch": 0}

    async def _fake_fetch_news(tickers, store=None):
        return []

    async def _fake_fetch_crypto_news(tickers, store=None):
        return []

    async def _fake_score_batch(articles, **kwargs):
        call_count["score_batch"] += 1
        return []

    monkeypatch.setattr(decision_loop.news_ingestion, "fetch_news", _fake_fetch_news)
    monkeypatch.setattr(decision_loop.news_ingestion, "fetch_crypto_news", _fake_fetch_crypto_news)
    monkeypatch.setattr(decision_loop.sentiment, "score_batch", _fake_score_batch)

    from app.database import SessionLocal
    from app.models import Portfolio

    db = SessionLocal()
    try:
        portfolio = Portfolio(user_id=None, owner_type="agent", risk_tolerance="low")
        db.add(portfolio)
        db.commit()
        db.refresh(portfolio)
        portfolio_id = portfolio.id
    finally:
        db.close()

    summary = asyncio.run(decision_loop.run_once(portfolio_id))

    assert call_count["score_batch"] == 0
    assert summary.articles_considered == 0
    assert summary.decisions_made == 0
    print(f"\nrun_once() with 0 new articles: score_batch called {call_count['score_batch']} time(s).")
