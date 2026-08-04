"""CRUD helpers for AgentDecision — the autonomous trading agent's audit log."""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import AgentDecision


def create_agent_decision(
    db: Session,
    *,
    portfolio_id: int,
    tickers: list[str],
    confidence: float,
    decision: str,
    reasoning: str,
    news_source: str | None = None,
    news_article_ids: list[str] | None = None,
    sentiment_score: float | None = None,
    trade_id: int | None = None,
    timestamp: datetime | None = None,
) -> AgentDecision:
    row = AgentDecision(
        portfolio_id=portfolio_id,
        timestamp=timestamp or datetime.utcnow(),
        tickers=tickers,
        news_source=news_source,
        news_article_ids=news_article_ids or [],
        sentiment_score=sentiment_score,
        confidence=confidence,
        decision=decision,
        reasoning=reasoning,
        trade_id=trade_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_agent_decisions(
    db: Session,
    portfolio_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[AgentDecision]:
    query = db.query(AgentDecision).filter(AgentDecision.portfolio_id == portfolio_id)
    if start is not None:
        query = query.filter(AgentDecision.timestamp >= start)
    if end is not None:
        query = query.filter(AgentDecision.timestamp <= end)
    return query.order_by(AgentDecision.timestamp.desc()).all()
