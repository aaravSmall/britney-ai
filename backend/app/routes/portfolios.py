"""Read-only portfolio endpoints (trade history, etc.)."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from app.deps import DbSession
from app.models import Portfolio, Trade
from app.schemas.trading import TradeOut

router = APIRouter(prefix="/portfolios", tags=["portfolios"])


@router.get("/{portfolio_id}/trades", response_model=list[TradeOut])
def get_trade_history(
    portfolio_id: int,
    db: DbSession,
    source: str | None = Query(
        None,
        pattern="^(user|agent)$",
        description='Filter by fill source: "user" or "agent". Omit for both.',
    ),
    limit: int = Query(50, gt=0, le=500, description="Max rows to return."),
    offset: int = Query(0, ge=0, description="Rows to skip, for pagination."),
    since: datetime | None = Query(
        None, description="Only trades at/after this ISO timestamp."
    ),
    until: datetime | None = Query(
        None, description="Only trades at/before this ISO timestamp."
    ),
) -> list[Trade]:
    """Trade history for a portfolio, newest first. 404s if the portfolio
    doesn't exist; returns [] if it exists but has no trades yet."""
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    query = db.query(Trade).filter(Trade.portfolio_id == portfolio_id)
    if source:
        query = query.filter(Trade.source == source)
    if since:
        query = query.filter(Trade.timestamp >= since)
    if until:
        query = query.filter(Trade.timestamp <= until)

    return (
        query.order_by(Trade.timestamp.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
