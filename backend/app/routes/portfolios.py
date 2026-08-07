"""Read-only portfolio endpoints (trade history, cross-portfolio summary
views for the dashboard's portfolio switcher, etc.)."""

from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from agent.decision_loop import ensure_target_portfolios
from app.deps import CurrentUser, DbSession
from app.models import Portfolio, PortfolioSnapshot, Trade
from app.schemas.dashboard import DashboardResponse, PortfolioListItem, SnapshotPointOut
from app.schemas.trading import TradeOut
from app.services.portfolio_service import build_portfolio_summary, get_or_create_portfolio
from app.services.portfolio_snapshot_service import get_snapshots

router = APIRouter(prefix="/portfolios", tags=["portfolios"])

# Mirrors agent/decision_loop.py's RISK_TOLERANCE_BY_TIER vocabulary —
# duplicated (not imported) since that's a fixed low/medium/high <->
# conservative/moderate/aggressive naming convention, not shared logic.
_TIER_LABELS: dict[str, str] = {
    "conservative": "Conservative (Agent)",
    "moderate": "Moderate (Agent)",
    "aggressive": "Aggressive (Agent)",
}

# Same shorthand as GET /dashboard/performance's `range` param, duplicated
# here rather than shared since it's a small, self-contained mapping and
# this router deliberately stays independent of app/routes/dashboard.py.
_RANGE_WINDOWS: dict[str, timedelta] = {
    "today": timedelta(days=1),
    "week": timedelta(days=7),
    "month30": timedelta(days=30),
    "fiveYear": timedelta(days=365 * 5),
}


@router.get("", response_model=list[PortfolioListItem])
def list_portfolios(db: DbSession, user: CurrentUser) -> list[PortfolioListItem]:
    """Portfolios the dashboard's portfolio switcher can show: the
    caller's own, plus the agent's three risk-tier model portfolios
    (created here on first call if they don't exist yet, same as the
    agent's own scheduler does — see ensure_target_portfolios)."""
    my_portfolio = get_or_create_portfolio(db, user)
    items = [
        PortfolioListItem(
            id=my_portfolio.id, label="My Portfolio", owner_type="user", risk_tolerance=None
        )
    ]
    agent_ids = ensure_target_portfolios(db)
    for tier, portfolio_id in agent_ids.items():
        items.append(
            PortfolioListItem(
                id=portfolio_id,
                label=_TIER_LABELS.get(tier, tier.title()),
                owner_type="agent",
                risk_tolerance=tier,
            )
        )
    return items


@router.get("/{portfolio_id}/summary", response_model=DashboardResponse)
async def get_portfolio_summary(portfolio_id: int, db: DbSession) -> DashboardResponse:
    """Dashboard-equivalent view (cash, holdings, total value, day change)
    for any portfolio — including the agent's — not just the caller's
    own. No auth on portfolio_id itself, same known gap as GET
    /portfolios/{id}/trades below (deferred to Sprint 4/5)."""
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    return await build_portfolio_summary(db, portfolio)


@router.get("/{portfolio_id}/performance", response_model=list[SnapshotPointOut])
def get_portfolio_performance(
    portfolio_id: int,
    db: DbSession,
    range_: str | None = Query(
        None,
        alias="range",
        pattern="^(today|week|month30|ytd|fiveYear)$",
        description=(
            "Shorthand matching the chart's range toggles "
            "(today/week/month30/ytd/fiveYear); translated into a `since` "
            "cutoff. Ignored if `since` is given."
        ),
    ),
    since: datetime | None = Query(
        None, description="Only snapshots at/after this ISO timestamp."
    ),
    until: datetime | None = Query(
        None, description="Only snapshots at/before this ISO timestamp."
    ),
) -> list[PortfolioSnapshot]:
    """Same shape and semantics as GET /dashboard/performance, for any
    portfolio_id rather than just the caller's own."""
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")

    effective_since = since
    if effective_since is None and range_:
        now = datetime.utcnow()
        if range_ == "today":
            effective_since = datetime(now.year, now.month, now.day)
        elif range_ == "ytd":
            effective_since = datetime(now.year, 1, 1)
        else:
            effective_since = now - _RANGE_WINDOWS[range_]

    return get_snapshots(db, portfolio.id, start=effective_since, end=until)


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
