"""Regression coverage for agent/decision_loop.py's run_once() per-ticker
execution-failure path.

Modeled directly on tests/test_rebalance.py's
test_insufficient_funds_error_rolls_back_and_leaves_no_orphaned_trade —
same bug mechanism, different call site: record_trade_fill() may already
have done db.add(trade) + db.flush() (a status="filled" insert) before
_settle_fill() raises InsufficientFundsError/InsufficientHoldingsError.
Without a db.rollback() in the except-clause around _execute() in
run_once()'s per-ticker loop, that orphaned insert used to get swept into
create_agent_decision()'s db.commit() a few lines later, in the very same
iteration — see decision_loop.py's except-clause comment for the fix.
"""

import asyncio
from datetime import datetime

import agent.decision_loop as decision_loop
from agent.news_ingestion import NewsArticle
from agent.sentiment import ScoreResult
from app.database import SessionLocal
from app.models import AgentDecision, Portfolio, Trade


def _new_agent_portfolio(cash_balance: float) -> int:
    """owner_type="test" (not "agent") deliberately — this is a standalone
    ad-hoc portfolio for run_once() to trade against, not one of the
    canonical three ensure_target_portfolios() manages. Using "agent"
    here would give agent/decision_loop.py's ensure_target_portfolios()
    (agent/decision_loop.py:120-145) a SECOND owner_type="agent",
    risk_tolerance="low" row alongside its own canonical one in this
    session-scoped shared test DB, which makes its `.one_or_none()`
    lookup raise MultipleResultsFound the next time anything calls it
    (e.g. GET /portfolios) — decision_loop._resolve_risk_tier() only
    reads portfolio.risk_tolerance directly (line 148-156), so it works
    identically regardless of owner_type; "test" just keeps this row out
    of every owner_type=="agent" query elsewhere."""
    db = SessionLocal()
    try:
        portfolio = Portfolio(
            user_id=None, owner_type="test", risk_tolerance="low", cash_balance=cash_balance
        )
        db.add(portfolio)
        db.commit()
        db.refresh(portfolio)
        return portfolio.id
    finally:
        db.close()


def test_execution_failure_rolls_back_and_leaves_no_orphaned_trade(client, monkeypatch):
    """CONSTRUCTED: conservative tier ("low" risk_tolerance), forced to
    decide "buy" on VOO via a mocked bullish/high-confidence article, with
    position_size_pct temporarily bumped to 2.0 (200% of cash) — an
    unambiguous, rounding-independent shortfall (same "real drift, not
    just the rounding edge case" spirit as the rebalance regression test),
    so this stays valid regardless of whatever quantity-rounding fix lands
    in a later step. _execute() must raise InsufficientFundsError, and
    that must NOT leave an orphaned status="filled" Trade row once
    create_agent_decision() commits a few lines later in the same
    iteration."""
    cash_balance = 100.0
    portfolio_id = _new_agent_portfolio(cash_balance)

    monkeypatch.setitem(decision_loop.RISK_PROFILES["conservative"], "position_size_pct", 2.0)

    async def _fake_fetch_news(tickers, store=None):
        return [
            NewsArticle(
                ticker="VOO",
                headline="VOO shares surge on record inflows",
                summary="Steady growth continues.",
                source="mock",
                url="https://example.com/voo",
                published_at=datetime.utcnow(),
                article_id="mock-voo-1",
            )
        ]

    async def _fake_fetch_crypto_news(tickers, store=None):
        return []

    async def _fake_score_batch(articles, **kwargs):
        return [
            ScoreResult(
                article_id=a.article_id,
                ticker=a.ticker,
                sentiment="bullish",
                confidence=0.90,  # above conservative's 0.75 threshold -> decision="buy"
                reasoning="Mock bullish score forcing a buy decision.",
            )
            for a in articles
        ]

    async def _fake_price(symbol, asset_type):
        return 100.0

    monkeypatch.setattr(decision_loop.news_ingestion, "fetch_news", _fake_fetch_news)
    monkeypatch.setattr(decision_loop.news_ingestion, "fetch_crypto_news", _fake_fetch_crypto_news)
    monkeypatch.setattr(decision_loop.sentiment, "score_batch", _fake_score_batch)
    monkeypatch.setattr(decision_loop.market_data, "get_price_for_holding", _fake_price)
    # Force the immediate-fill _execute() path (not the off-hours _queue()
    # path, which has no flush-then-raise step at all) regardless of
    # whatever time this test actually runs at.
    monkeypatch.setattr(decision_loop, "is_market_hours", lambda: True)

    summary = asyncio.run(decision_loop.run_once(portfolio_id))

    assert summary.trades_executed == 0  # the buy correctly never went through
    assert summary.decisions_made == 1

    db = SessionLocal()
    try:
        orphaned = (
            db.query(Trade)
            .filter(Trade.portfolio_id == portfolio_id, Trade.symbol == "VOO")
            .all()
        )
        assert orphaned == []  # NOT orphaned — rollback cleared the flushed insert

        portfolio = db.get(Portfolio, portfolio_id)
        assert portfolio.cash_balance == cash_balance  # untouched — no partial fill effect

        # create_agent_decision() ran a few lines after the caught exception,
        # on the same session — proves the rollback didn't poison it.
        decisions = (
            db.query(AgentDecision)
            .filter(AgentDecision.portfolio_id == portfolio_id)
            .all()
        )
        assert len(decisions) == 1
        assert decisions[0].decision == "hold"
        assert "Execution failed, held instead" in decisions[0].reasoning
        assert decisions[0].trade_id is None

        print(
            "\nExecution-failure regression: 0 orphaned VOO trade rows, "
            f"cash_balance unchanged (${portfolio.cash_balance}), "
            "1 AgentDecision row committed cleanly with decision='hold'."
        )
    finally:
        db.close()
