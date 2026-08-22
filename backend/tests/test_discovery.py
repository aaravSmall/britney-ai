"""Coverage for the general-news discovery pipeline
(agent/discovery.py, app/services/discovery_service.py,
agent/run_discovery.py) — see docs/DISCOVERY_DESIGN.md.

Three things this file exists to guarantee, none of which live-network
tests (tests/test_stocks.py) or the manual verification run cover on
every CI run:

1. The three validation gates (a: real quote, b: name match, c: not
   already a fixed target ticker) reject in the documented order and
   never let an unvalidated ticker report as "passed."
2. company_name_matches()'s token-overlap fuzzy match behaves
   correctly on real-shaped company-name pairs, with no external
   fuzzy-match library to trust.
3. Ticker-level, day-scoped dedup (discovery_service.was_ticker_discovered_today)
   actually prevents a same-day duplicate mention from producing a
   second DiscoveredCandidate row.

Stock quotes are monkeypatched (same convention test_rebalance.py/
test_pending_trades.py already use for market_data.get_price_for_holding)
rather than hitting live Yahoo — deterministic, no network dependency.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

import app.services.stock_data as stock_data
from agent import discovery
from agent.discovery import (
    ExtractionResult,
    company_name_matches,
    validate_candidate,
)
from agent.news_ingestion import NewsArticle, SeenArticleStore
from app.database import SessionLocal
from app.services import discovery_service


# ---------------------------------------------------------------------
# company_name_matches() — pure function, no monkeypatching needed
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "quote_name,claimed_name,expected",
    [
        ("Apple Inc.", "Apple Inc.", True),
        ("Apple Inc.", "Apple", True),
        ("NVIDIA Corporation", "Nvidia", True),
        ("Tesla, Inc.", "Tesla Motors", True),
        ("Meta Platforms, Inc.", "Meta Materials Inc.", False),  # real mismatch case
        ("Apple Inc.", "Microsoft Corporation", False),
        ("", "Apple Inc.", False),
        ("Apple Inc.", "", False),
        # Real false rejections caught live in production (2026-08-22, see
        # docs/OVERVIEW.md's "AI model performance report" and
        # memory/project_discovery_gateb_false_rejects.md) — a compound
        # name written as one word by one source, multiple by the other.
        ("JP Morgan Chase & Co.", "JPMorgan Chase & Co.", True),
        ("ExxonMobil Holdings Corporation", "Exxon Mobil Corporation", True),
        # SIEGY: real false rejection (2026-08-19), fixed by adding
        # "ag"/"aktiengesellschaft" to _SUFFIX_TOKENS — AG is the standard
        # abbreviation for Aktiengesellschaft, not a naming error.
        ("Siemens Aktiengesellschaft", "Siemens AG", True),
        # RR/PARA: real TRUE rejections (2026-08-17, 2026-08-20) — the LLM
        # named a real company but guessed the wrong ticker for it. Must
        # not flip after the suffix-list change above.
        ("Richtech Robotics Inc.", "Rolls-Royce Holdings plc", False),
        ("Banzai International, Inc.", "Paramount Global", False),
        # RTX/2222.SR: real rejections (2026-08-17, 2026-08-20) that ARE
        # the same company as the ticker (a 2023 corporate rename; a brand
        # name vs. formal legal name) but score zero/borderline token
        # overlap for reasons no suffix-stripping fixes — deliberately
        # left rejected here. Fixing these needs a company alias/rename
        # table, a separate, larger feature — see docs/IDEAS.txt.
        ("RTX Corporation", "Raytheon Technologies Corporation", False),
        ("Saudi Arabian Oil Company", "Saudi Aramco", False),
    ],
)
def test_company_name_matches(quote_name, claimed_name, expected):
    assert company_name_matches(quote_name, claimed_name) is expected


# ---------------------------------------------------------------------
# validate_candidate() — the three hard gates, in order
# ---------------------------------------------------------------------


@pytest.fixture
def fake_quote(monkeypatch):
    """Monkeypatches stock_data.quote() (which discovery.validate_candidate()
    calls) with a small fixed lookup table, same convention
    test_rebalance.py's fixed_prices fixture uses for get_price_for_holding."""
    table: dict[str, dict | None] = {}

    async def _fake(ticker: str):
        return table.get(ticker.upper())

    monkeypatch.setattr(stock_data, "quote", _fake)
    return table


def test_gate_a_rejects_when_quote_returns_none(fake_quote):
    """No quote at all (unknown/fake ticker, or a transient fetch
    failure — validate_candidate() can't and shouldn't try to tell
    those apart, quote() already logs which internally)."""
    result = asyncio.run(validate_candidate("ZZZZFAKE", "Not A Real Company"))
    assert result.passed is False
    assert result.rejected_gate == "a"


def test_gate_b_rejects_on_company_name_mismatch(fake_quote):
    """A real ticker exists, but the LLM's claimed company doesn't match
    it — the "LLM named a real company but guessed the wrong ticker"
    failure mode this gate exists to catch."""
    fake_quote["MMAT"] = {"company_name": "Meta Materials Inc."}
    result = asyncio.run(validate_candidate("MMAT", "Meta Platforms"))
    assert result.passed is False
    assert result.rejected_gate == "b"
    assert result.quote_company_name == "Meta Materials Inc."


def test_gate_c_rejects_fixed_target_ticker(fake_quote):
    """A real, correctly-named ticker that's already in
    news_ingestion.TARGET_PORTFOLIOS isn't "discovery.\""""
    fake_quote["AAPL"] = {"company_name": "Apple Inc."}
    result = asyncio.run(validate_candidate("AAPL", "Apple Inc."))
    assert result.passed is False
    assert result.rejected_gate == "c"


def test_all_gates_pass_for_a_real_new_ticker(fake_quote):
    fake_quote["NVDA"] = {"company_name": "NVIDIA Corporation"}
    result = asyncio.run(validate_candidate("NVDA", "Nvidia"))
    assert result.passed is True
    assert result.rejected_gate is None
    assert result.ticker == "NVDA"


# ---------------------------------------------------------------------
# Article-level dedup (SeenArticleStore, reused as-is from news_ingestion.py)
# ---------------------------------------------------------------------


@pytest.fixture
def seen_store(tmp_path):
    store = SeenArticleStore(db_path=tmp_path / "test_discovery_seen.sqlite3")
    yield store
    store.close()


def test_fetch_general_news_filters_already_seen_articles(seen_store, monkeypatch):
    """Same guarantee tests/test_agent_dedup.py already covers for
    fetch_news()/fetch_crypto_news(), for fetch_general_news() — mock
    mode (no FINNHUB_API_KEY) returns deterministic articles, and a
    second fetch against the same persisted store must exclude anything
    already marked seen."""

    class _NoKeySettings:
        finnhub_api_key = ""

    monkeypatch.setattr(discovery, "get_settings", lambda: _NoKeySettings())

    first, _ = asyncio.run(discovery.fetch_general_news(0, store=seen_store))
    assert len(first) == 2  # _mock_general_articles() always returns 2

    for article in first:
        seen_store.mark_seen(article.article_id)

    second, _ = asyncio.run(discovery.fetch_general_news(0, store=seen_store))
    assert second == []


# ---------------------------------------------------------------------
# Ticker-level, day-scoped dedup (discovery_service — DB-backed)
# ---------------------------------------------------------------------


def test_ticker_level_dedup_prevents_same_day_duplicate_candidate(client):
    """Constructs the exact same-day duplicate-mention case
    docs/DISCOVERY_DESIGN.md §6 flags: two different articles about the
    same company on the same day must only ever produce ONE validated
    DiscoveredCandidate row, not two.

    Takes the (unused) `client` fixture purely for its side effect —
    same convention test_rebalance.py's client-taking tests rely on:
    instantiating TestClient(app) runs app/main.py's lifespan, which is
    what actually calls bootstrap_schema() and creates
    discovered_candidates (a raw SessionLocal() with no schema created
    yet would 500 on the first INSERT)."""
    db = SessionLocal()
    try:
        ticker = f"TESTDUP{int(datetime.utcnow().timestamp())}"  # unique per test run
        assert discovery_service.was_ticker_discovered_today(db, ticker) is False

        first = discovery_service.create_discovered_candidate(
            db,
            ticker=ticker,
            company_name="Test Duplicate Corp.",
            source_article_id="finnhub-general:1001",
            source_headline="Test Duplicate Corp posts a surprise earnings beat",
            confidence=0.7,
        )
        assert first.id is not None

        # Second article, same ticker, same (UTC) day — the scenario
        # run_discovery.py's cycle loop checks was_ticker_discovered_today()
        # for BEFORE calling validate_candidate() again.
        assert discovery_service.was_ticker_discovered_today(db, ticker) is True

        count = (
            db.query(discovery_service.DiscoveredCandidate)
            .filter(discovery_service.DiscoveredCandidate.ticker == ticker)
            .count()
        )
        assert count == 1
    finally:
        db.close()


def test_ticker_level_dedup_does_not_block_a_different_ticker(client):
    db = SessionLocal()
    try:
        ticker_a = f"TESTA{int(datetime.utcnow().timestamp())}"
        ticker_b = f"TESTB{int(datetime.utcnow().timestamp())}"
        discovery_service.create_discovered_candidate(
            db,
            ticker=ticker_a,
            company_name="Test A Corp.",
            source_article_id="finnhub-general:2001",
            source_headline="Test A Corp headline",
            confidence=0.6,
        )
        assert discovery_service.was_ticker_discovered_today(db, ticker_b) is False
    finally:
        db.close()


def test_ticker_level_dedup_scoped_to_today_not_older_days(client):
    """A candidate discovered yesterday must not block today's
    discovery of the same ticker — this is a daily, not permanent,
    dedup window (see docs/DISCOVERY_DESIGN.md §6)."""
    db = SessionLocal()
    try:
        ticker = f"TESTOLD{int(datetime.utcnow().timestamp())}"
        candidate = discovery_service.create_discovered_candidate(
            db,
            ticker=ticker,
            company_name="Test Old Corp.",
            source_article_id="finnhub-general:3001",
            source_headline="Test Old Corp headline",
            confidence=0.5,
        )
        # Backdate it to yesterday.
        candidate.discovered_at = datetime.utcnow() - timedelta(days=1)
        db.add(candidate)
        db.commit()

        assert discovery_service.was_ticker_discovered_today(db, ticker) is False
    finally:
        db.close()


# ---------------------------------------------------------------------
# extract_companies_batch() mock-mode fallback
# ---------------------------------------------------------------------


def test_extract_companies_batch_mock_mode(monkeypatch):
    class _NoKeySettings:
        openai_api_key = ""

    monkeypatch.setattr(discovery, "get_settings", lambda: _NoKeySettings())

    articles = [
        NewsArticle(
            ticker="",
            headline="Nvidia shares climb after analysts raise price targets",
            summary="mock summary",
            source="mock",
            url="",
            published_at=datetime.utcnow(),
            article_id="finnhub-general:1",
        ),
        NewsArticle(
            ticker="",
            headline="Fed holds rates steady",
            summary="mock summary, no company",
            source="mock",
            url="",
            published_at=datetime.utcnow(),
            article_id="finnhub-general:2",
        ),
    ]

    results = asyncio.run(discovery.extract_companies_batch(articles))
    assert len(results) == 2
    assert isinstance(results[0], ExtractionResult)
    assert results[0].ticker_guess == "NVDA"
    assert results[1].ticker_guess is None
