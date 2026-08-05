"""
Core decision loop for the britney.ai autonomous trading agent.

Ties together news_ingestion -> sentiment -> per-risk-profile decision
rules -> trade execution -> AgentDecision logging -> PortfolioSnapshot,
for one portfolio at a time.

Unlike news_ingestion.py/sentiment.py, this module is NOT side-effect-free
by design — its whole job is to write real Trade/AgentDecision/
PortfolioSnapshot rows — so it does import the FastAPI app's database and
services rather than staying standalone. It reuses the exact same
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
from agent.news_ingestion import NewsArticle
from agent.sentiment import ScoreResult
from app.database import SessionLocal
from app.models import Portfolio, Trade, User
from app.services import market_data
from app.services.agent_decision_service import create_agent_decision
from app.services.portfolio_service import (
    InsufficientFundsError,
    InsufficientHoldingsError,
    get_or_create_portfolio,
    record_trade_fill,
)
from app.services.portfolio_snapshot_service import create_snapshot
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

# Synthetic users backing the three agent-managed model portfolios —
# distinct from real signed-up users, since Portfolio.user_id is a
# required unique FK and the agent needs *a* portfolio to run against for
# each risk tier regardless of whether any real user has that profile.
AGENT_USER_SEEDS: dict[str, dict[str, str]] = {
    "conservative": {
        "firebase_uid": "agent-conservative",
        "email": "agent-conservative@britney.ai.local",
        "name": "Agent — Conservative",
        "risk_tolerance": "low",
    },
    "moderate": {
        "firebase_uid": "agent-moderate",
        "email": "agent-moderate@britney.ai.local",
        "name": "Agent — Moderate",
        "risk_tolerance": "medium",
    },
    "aggressive": {
        "firebase_uid": "agent-aggressive",
        "email": "agent-aggressive@britney.ai.local",
        "name": "Agent — Aggressive",
        "risk_tolerance": "high",
    },
}


@dataclass
class RunSummary:
    portfolio_id: int
    risk_tier: str
    articles_considered: int
    decisions_made: int
    trades_executed: int
    snapshot_total_value: float


def ensure_target_portfolios(db: Session) -> dict[str, int]:
    """Idempotently create (or fetch) the three agent-managed model
    portfolios, one per risk tier. Safe to call every run_agent.py tick —
    a portfolio that's never been created yet (this is the agent's own
    first-ever run, not a "never loaded /dashboard" user) is created here
    with default cash and zero holdings, same as get_or_create_portfolio
    does for a real user; it is deliberately NOT seeded with the
    demo-mode holdings from ensure_demo_holdings (VOO/AAPL/BTC) — those
    exist purely so a fresh human demo login has something to look at, and
    would misrepresent the agent's own trading history if applied here.
    """
    portfolio_ids: dict[str, int] = {}
    for tier, seed in AGENT_USER_SEEDS.items():
        user = (
            db.query(User).filter(User.firebase_uid == seed["firebase_uid"]).one_or_none()
        )
        if user is None:
            user = User(
                firebase_uid=seed["firebase_uid"],
                email=seed["email"],
                name=seed["name"],
                risk_tolerance=seed["risk_tolerance"],
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        portfolio = get_or_create_portfolio(db, user)
        portfolio_ids[tier] = portfolio.id
    return portfolio_ids


def _resolve_risk_tier(portfolio: Portfolio) -> str:
    tolerance = (portfolio.user.risk_tolerance or "").lower()
    return RISK_TIER_BY_TOLERANCE.get(tolerance, DEFAULT_RISK_TIER)


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
        holding = next((h for h in portfolio.holdings if h.symbol == ticker), None)
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
    price = await market_data.get_price_for_holding(ticker, "stock")
    if price is None:
        raise ValueError(f"No price available for {ticker}")

    if side == "buy":
        dollar_amount = portfolio.cash_balance * profile["position_size_pct"]
        quantity = round(dollar_amount / price, 6)
    else:  # sell
        holding = next(h for h in portfolio.holdings if h.symbol == ticker)
        quantity = round(holding.quantity * profile["position_size_pct"], 6)

    if quantity <= 0:
        raise ValueError(f"Computed {side} quantity for {ticker} was zero.")

    result = execute_trade(ticker, "stock", side, quantity, simulate_only=True)
    if result.status != "filled":
        raise ValueError(f"Execution did not fill: {result.message}")

    return record_trade_fill(
        db,
        portfolio,
        ticker,
        "stock",
        side,
        quantity,
        price,
        simulated=result.simulated,
        order_id=result.order_id,
        source="agent",
    )


async def _take_snapshot(db: Session, portfolio: Portfolio) -> float:
    holdings_map: dict[str, float] = {}
    total_value = portfolio.cash_balance
    for h in portfolio.holdings:
        price = await market_data.get_price_for_holding(h.symbol, h.asset_type)
        if price is None:
            price = h.last_price or h.avg_cost
        total_value += h.quantity * price
        holdings_map[h.symbol] = h.quantity

    create_snapshot(
        db,
        portfolio_id=portfolio.id,
        total_value=round(total_value, 2),
        cash=portfolio.cash_balance,
        holdings=holdings_map,
    )
    return total_value


async def run_once(portfolio_id: int) -> RunSummary:
    """Run one full decision cycle for a single portfolio: fetch news for
    its risk tier's tickers, score it, decide buy/sell/hold per ticker,
    execute + log each decision, then snapshot the portfolio's value.

    Safe to call for a portfolio that has never been loaded via
    /dashboard: it only reads/writes Portfolio/PortfolioHolding/Trade rows
    that already exist by the time this runs (ensure_target_portfolios, or
    get_or_create_portfolio for a real user, is expected to have created
    the Portfolio already) — record_trade_fill takes a resolved Portfolio
    directly rather than re-deriving one from a User, so there's no path
    left where a scheduled agent run could hit a missing portfolio.
    """
    db = SessionLocal()
    try:
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).one_or_none()
        if portfolio is None:
            raise ValueError(f"No portfolio with id={portfolio_id}")

        risk_tier = _resolve_risk_tier(portfolio)
        profile = RISK_PROFILES[risk_tier]
        tickers = news_ingestion.target_stock_tickers_for(risk_tier)

        articles = await news_ingestion.fetch_news(tickers=tickers)

        decisions_made = 0
        trades_executed = 0

        if articles:
            scores = await sentiment.score_batch(articles)

            # One decision per ticker per run: take the highest-confidence
            # article as the driving signal, but keep every article id
            # seen for that ticker this run for full auditability.
            driving: dict[str, tuple[NewsArticle, ScoreResult]] = {}
            article_ids_by_ticker: dict[str, list[str]] = {}
            for article, score in zip(articles, scores):
                article_ids_by_ticker.setdefault(article.ticker, []).append(
                    article.article_id
                )
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

                create_agent_decision(
                    db,
                    portfolio_id=portfolio.id,
                    tickers=[ticker],
                    confidence=score.confidence,
                    decision=decision,
                    reasoning=reasoning,
                    news_source=article.source,
                    news_article_ids=article_ids_by_ticker[ticker],
                    sentiment_score=_signed_sentiment_score(score),
                    trade_id=trade.id if trade else None,
                )
                decisions_made += 1

        db.refresh(portfolio)
        total_value = await _take_snapshot(db, portfolio)

        return RunSummary(
            portfolio_id=portfolio.id,
            risk_tier=risk_tier,
            articles_considered=len(articles),
            decisions_made=decisions_made,
            trades_executed=trades_executed,
            snapshot_total_value=round(total_value, 2),
        )
    finally:
        db.close()


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
            f"trades={summary.trades_executed} total_value=${summary.snapshot_total_value:,.2f}"
        )


if __name__ == "__main__":
    asyncio.run(_main())
