"""
Core decision loop for the britney.ai autonomous trading agent.

Ties together news_ingestion -> sentiment -> per-risk-profile decision
rules -> trade execution -> AgentDecision logging, for one portfolio at a
time. Portfolio value snapshots are a separate concern handled by
agent/snapshot.py, called from run_agent.py once per portfolio after each
poll cycle regardless of what this module decided.

Unlike news_ingestion.py/sentiment.py, this module is NOT side-effect-free
by design — its whole job is to write real Trade/AgentDecision rows — so
it does import the FastAPI app's database and services rather than
staying standalone. It reuses the exact same
execute_trade()/record_trade_fill() path the HTTP /trading/trade endpoint
uses, just called in-process instead of over HTTP (there's no browser
session/auth token in a scheduled background run).

Run once, for one portfolio:

    cd backend && python -m agent.decision_loop <portfolio_id>

Run once for (creating if needed) the three agent-managed model
portfolios, one per risk tier:

    cd backend && python -m agent.decision_loop
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, time as dtime

from sqlalchemy.orm import Session

from agent import news_ingestion, sentiment
from agent.news_ingestion import NewsArticle, SeenArticleStore
from agent.sentiment import ScoreResult
from app.database import SessionLocal
from app.models import Portfolio, Trade
from app.services import market_data
from app.services.agent_decision_service import create_agent_decision
from app.services.portfolio_service import (
    InsufficientFundsError,
    InsufficientHoldingsError,
    record_trade_fill,
)
from app.trading.execution import execute_trade

# Maps User.risk_tolerance ("low" | "medium" | "high", set during
# onboarding) onto the three news_ingestion.TARGET_PORTFOLIOS buckets.
RISK_TIER_BY_TOLERANCE: dict[str, str] = {
    "low": "conservative",
    "medium": "moderate",
    "high": "aggressive",
}
DEFAULT_RISK_TIER = "moderate"  # used if a user never set risk_tolerance

RISK_PROFILES: dict[str, dict[str, float | int]] = {
    "conservative": {
        "confidence_threshold": 0.75,
        "position_size_pct": 0.02,
        "max_trades_per_day": 1,
    },
    "moderate": {
        "confidence_threshold": 0.60,
        "position_size_pct": 0.05,
        "max_trades_per_day": 3,
    },
    "aggressive": {
        "confidence_threshold": 0.50,
        "position_size_pct": 0.10,
        "max_trades_per_day": 6,
    },
}

# Inverse of RISK_TIER_BY_TOLERANCE — the risk_tolerance value to store on
# each agent-managed model portfolio, one per tier.
RISK_TOLERANCE_BY_TIER: dict[str, str] = {v: k for k, v in RISK_TIER_BY_TOLERANCE.items()}

# Ticker -> asset_type ("stock" | "crypto"), derived from
# news_ingestion.TARGET_PORTFOLIOS rather than duplicated by hand — used
# by _execute() to price/execute/record each ticker correctly instead of
# assuming "stock". Deliberately keyed by ticker, not risk tier: all three
# tiers hold both a stock/ETF ticker and a crypto ticker, so tier alone
# can't tell you which one a given decision is for.
_ASSET_TYPE_BY_TICKER: dict[str, str] = {
    symbol: asset_type
    for assets in news_ingestion.TARGET_PORTFOLIOS.values()
    for symbol, asset_type in assets
}


def _asset_type_for(ticker: str) -> str:
    return _ASSET_TYPE_BY_TICKER.get(ticker, "stock")


@dataclass
class RunSummary:
    portfolio_id: int
    risk_tier: str
    articles_considered: int
    decisions_made: int
    trades_executed: int


def ensure_target_portfolios(db: Session) -> dict[str, int]:
    """Idempotently create (or fetch) the three agent-managed model
    portfolios, one per risk tier — owner_type="agent", user_id=None, no
    backing User row at all (Portfolio.user_id is nullable specifically
    for this). Safe to call every run_agent.py tick: a portfolio that's
    never been created yet (this is the agent's own first-ever run) is
    created here with default cash and zero holdings; it is deliberately
    NOT seeded with the demo-mode holdings from ensure_demo_holdings
    (VOO/AAPL/BTC) — those exist purely so a fresh human demo login has
    something to look at, and would misrepresent the agent's own trading
    history if applied here.
    """
    portfolio_ids: dict[str, int] = {}
    for tier, tolerance in RISK_TOLERANCE_BY_TIER.items():
        portfolio = (
            db.query(Portfolio)
            .filter(Portfolio.owner_type == "agent", Portfolio.risk_tolerance == tolerance)
            .one_or_none()
        )
        if portfolio is None:
            portfolio = Portfolio(user_id=None, owner_type="agent", risk_tolerance=tolerance)
            db.add(portfolio)
            db.commit()
            db.refresh(portfolio)
        portfolio_ids[tier] = portfolio.id
    return portfolio_ids


def _resolve_risk_tier(portfolio: Portfolio) -> str:
    """Agent portfolios carry risk_tolerance directly; a real user's
    portfolio has that column NULL and reads it off the owning User
    instead (portfolio.user is None for agent portfolios, hence the
    guard)."""
    tolerance = portfolio.risk_tolerance
    if not tolerance and portfolio.user is not None:
        tolerance = portfolio.user.risk_tolerance
    return RISK_TIER_BY_TOLERANCE.get((tolerance or "").lower(), DEFAULT_RISK_TIER)


def _signed_sentiment_score(score: ScoreResult) -> float:
    if score.sentiment == "bullish":
        return score.confidence
    if score.sentiment == "bearish":
        return -score.confidence
    return 0.0


def _count_agent_trades_today(db: Session, portfolio_id: int) -> int:
    start_of_day = datetime.combine(datetime.utcnow().date(), dtime.min)
    return (
        db.query(Trade)
        .filter(
            Trade.portfolio_id == portfolio_id,
            Trade.source == "agent",
            Trade.timestamp >= start_of_day,
        )
        .count()
    )


def _decide(
    portfolio: Portfolio,
    ticker: str,
    score: ScoreResult,
    profile: dict[str, float | int],
    *,
    trades_today: int,
) -> tuple[str, str]:
    """Pure decision rule: given the driving article's score for `ticker`
    and this portfolio's risk profile, return (decision, reasoning_note).
    Does not touch the DB or execute anything — that's the caller's job,
    so this stays easy to reason about/test on its own."""
    if trades_today >= profile["max_trades_per_day"]:
        return "hold", f"Daily agent trade cap reached ({profile['max_trades_per_day']})."

    if score.confidence <= profile["confidence_threshold"]:
        return (
            "hold",
            f"Confidence {score.confidence:.2f} at/below threshold "
            f"{profile['confidence_threshold']} for this risk tier.",
        )

    if score.sentiment == "bullish":
        return "buy", ""

    if score.sentiment == "bearish":
        asset_type = _asset_type_for(ticker)
        holding = next(
            (h for h in portfolio.holdings if h.symbol == ticker and h.asset_type == asset_type),
            None,
        )
        if not holding or holding.quantity <= 0:
            return "hold", f"Bearish signal but no existing {ticker} position to sell."
        return "sell", ""

    return "hold", "Neutral sentiment."


async def _execute(
    db: Session,
    portfolio: Portfolio,
    ticker: str,
    side: str,
    profile: dict[str, float | int],
) -> Trade:
    asset_type = _asset_type_for(ticker)
    price = await market_data.get_price_for_holding(ticker, asset_type)
    if price is None:
        raise ValueError(f"No price available for {ticker}")

    if side == "buy":
        dollar_amount = portfolio.cash_balance * profile["position_size_pct"]
        quantity = round(dollar_amount / price, 6)
    else:  # sell
        holding = next(
            h for h in portfolio.holdings if h.symbol == ticker and h.asset_type == asset_type
        )
        quantity = round(holding.quantity * profile["position_size_pct"], 6)

    if quantity <= 0:
        raise ValueError(f"Computed {side} quantity for {ticker} was zero.")

    result = execute_trade(ticker, asset_type, side, quantity, simulate_only=True)
    if result.status != "filled":
        raise ValueError(f"Execution did not fill: {result.message}")

    return record_trade_fill(
        db,
        portfolio,
        ticker,
        asset_type,
        side,
        quantity,
        price,
        simulated=result.simulated,
        order_id=result.order_id,
        source="agent",
    )


async def run_once(portfolio_id: int) -> RunSummary:
    """Run one full decision cycle for a single portfolio: fetch news for
    its risk tier's tickers, score it, decide buy/sell/hold per ticker, and
    execute + log each decision. Does not itself snapshot the portfolio's
    value — see agent/snapshot.py, called separately by run_agent.py.

    Safe to call for a portfolio that has never been loaded via
    /dashboard: it only reads/writes Portfolio/PortfolioHolding/Trade rows
    that already exist by the time this runs (ensure_target_portfolios, or
    get_or_create_portfolio for a real user, is expected to have created
    the Portfolio already) — record_trade_fill takes a resolved Portfolio
    directly rather than re-deriving one from a User, so there's no path
    left where a scheduled agent run could hit a missing portfolio.
    """
    db = SessionLocal()
    store = SeenArticleStore()
    try:
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).one_or_none()
        if portfolio is None:
            raise ValueError(f"No portfolio with id={portfolio_id}")

        risk_tier = _resolve_risk_tier(portfolio)
        profile = RISK_PROFILES[risk_tier]
        stock_tickers = news_ingestion.target_stock_tickers_for(risk_tier)
        crypto_tickers = news_ingestion.target_crypto_tickers_for(risk_tier)

        # fetch_news()/fetch_crypto_news() do not mark articles processed
        # themselves (see SeenArticleStore's docstring) — this loop marks
        # each ticker's article ids seen only after create_agent_decision()
        # durably commits a decision for that ticker, below. If anything
        # raises before that point for a given ticker (execution bug, DB
        # error, process crash), its articles stay unmarked and are
        # retried on the next scheduled run instead of being silently
        # lost. Two separate fetch calls (not a merged ticker list) since
        # stock and crypto news come from different providers with
        # different request shapes — see news_ingestion.py.
        stock_articles = await news_ingestion.fetch_news(tickers=stock_tickers, store=store)
        crypto_articles = await news_ingestion.fetch_crypto_news(
            tickers=crypto_tickers, store=store
        )
        articles = stock_articles + crypto_articles

        decisions_made = 0
        trades_executed = 0

        if articles:
            scores = await sentiment.score_batch(articles)

            # One decision per ticker per run: take the highest-confidence
            # article as the driving signal, but keep every (article, score)
            # pair seen for that ticker this run — not just their ids — so
            # create_agent_decision() below can persist full citations
            # (headline/source/url/published date) and each article's own
            # sentiment score, not only the driving one's.
            driving: dict[str, tuple[NewsArticle, ScoreResult]] = {}
            scored_by_ticker: dict[str, list[tuple[NewsArticle, ScoreResult]]] = {}
            for article, score in zip(articles, scores):
                scored_by_ticker.setdefault(article.ticker, []).append((article, score))
                best = driving.get(article.ticker)
                if best is None or score.confidence > best[1].confidence:
                    driving[article.ticker] = (article, score)

            trades_today = _count_agent_trades_today(db, portfolio.id)

            for ticker, (article, score) in driving.items():
                decision, note = _decide(
                    portfolio, ticker, score, profile, trades_today=trades_today
                )

                trade: Trade | None = None
                if decision in ("buy", "sell"):
                    try:
                        trade = await _execute(db, portfolio, ticker, decision, profile)
                        trades_today += 1
                        trades_executed += 1
                    except (
                        InsufficientFundsError,
                        InsufficientHoldingsError,
                        ValueError,
                    ) as e:
                        decision = "hold"
                        note = f"Execution failed, held instead: {e}"

                reasoning = score.reasoning
                if note:
                    reasoning = f"{reasoning} {note}".strip()

                ticker_scored = scored_by_ticker[ticker]
                create_agent_decision(
                    db,
                    portfolio_id=portfolio.id,
                    tickers=[ticker],
                    confidence=score.confidence,
                    decision=decision,
                    reasoning=reasoning,
                    news_source=article.source,
                    news_article_ids=[a.article_id for a, _ in ticker_scored],
                    articles=[
                        {
                            "article_id": a.article_id,
                            "ticker": a.ticker,
                            "headline": a.headline,
                            "source": a.source,
                            "url": a.url,
                            "published_at": a.published_at.isoformat(),
                            "sentiment": s.sentiment,
                            "confidence": s.confidence,
                            "reasoning": s.reasoning,
                        }
                        for a, s in ticker_scored
                    ],
                    sentiment_score=_signed_sentiment_score(score),
                    trade_id=trade.id if trade else None,
                )
                decisions_made += 1

                # Only now — after the decision is durably committed — mark
                # this ticker's articles processed. A crash/exception above
                # this line leaves them unmarked for retry next run instead
                # of vanishing from the dedupe cache unprocessed.
                for a, _ in ticker_scored:
                    store.mark_seen(a.article_id)

        return RunSummary(
            portfolio_id=portfolio.id,
            risk_tier=risk_tier,
            articles_considered=len(articles),
            decisions_made=decisions_made,
            trades_executed=trades_executed,
        )
    finally:
        db.close()
        store.close()


async def _main() -> None:
    if len(sys.argv) > 1:
        portfolio_ids = [int(sys.argv[1])]
    else:
        db = SessionLocal()
        try:
            portfolio_ids = list(ensure_target_portfolios(db).values())
        finally:
            db.close()

    for pid in portfolio_ids:
        summary = await run_once(pid)
        print(
            f"portfolio={summary.portfolio_id} tier={summary.risk_tier} "
            f"articles={summary.articles_considered} decisions={summary.decisions_made} "
            f"trades={summary.trades_executed}"
        )


if __name__ == "__main__":
    asyncio.run(_main())
