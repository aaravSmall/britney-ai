"""Read-only portfolio endpoints (trade history, cross-portfolio summary
views for the dashboard's portfolio switcher, etc.)."""

from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Query

from agent.decision_loop import ensure_target_portfolios
from app.deps import DbSession, OptionalCurrentUser, resolve_current_user
from app.models import Portfolio, PortfolioSnapshot, Trade
from app.schemas.dashboard import (
    DashboardResponse,
    HoldingPricePointOut,
    PortfolioListItem,
    SnapshotPointOut,
)
from app.schemas.trading import TradeOut
from app.services.portfolio_service import build_portfolio_summary, get_or_create_portfolio
from app.services.portfolio_snapshot_service import get_snapshots, get_symbol_price_history

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


def _require_owner_if_user_portfolio(
    db,
    portfolio: Portfolio,
    authorization: str | None,
) -> None:
    """Agent portfolios (owner_type="agent") stay publicly readable as a
    transparency feature — no auth required. User portfolios require a
    valid token AND that the token's user owns this exact portfolio: 401
    if the token is missing/invalid, 403 (not 404 — no need to hide
    existence) if it's valid but for a different user."""
    if portfolio.owner_type != "user":
        return
    current_user = resolve_current_user(db, authorization)
    if portfolio.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your portfolio")


@router.get("", response_model=list[PortfolioListItem])
def list_portfolios(db: DbSession, user: OptionalCurrentUser) -> list[PortfolioListItem]:
    """Portfolios the dashboard's portfolio switcher can show: the
    agent's three risk-tier model portfolios always (created here on
    first call if they don't exist yet, same as the agent's own
    scheduler does — see ensure_target_portfolios), plus the caller's own
    if authenticated. Unauthenticated callers just get the three agent
    portfolios — no error — so the switcher still works for a logged-out
    demo view of the agent's performance."""
    items: list[PortfolioListItem] = []
    if user is not None:
        my_portfolio = get_or_create_portfolio(db, user)
        items.append(
            PortfolioListItem(
                id=my_portfolio.id, label="My Portfolio", owner_type="user", risk_tolerance=None
            )
        )
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
async def get_portfolio_summary(
    portfolio_id: int,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> DashboardResponse:
    """Dashboard-equivalent view (cash, holdings, total value, day change)
    for any portfolio. Agent portfolios are publicly readable; a user
    portfolio requires the owning user's token (401 missing/invalid, 403
    wrong user)."""
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    _require_owner_if_user_portfolio(db, portfolio, authorization)
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
    authorization: Annotated[str | None, Header()] = None,
) -> list[PortfolioSnapshot]:
    """Same shape and semantics as GET /dashboard/performance, for any
    portfolio_id rather than just the caller's own. Agent portfolios are
    publicly readable; a user portfolio requires the owning user's token
    (401 missing/invalid, 403 wrong user)."""
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    _require_owner_if_user_portfolio(db, portfolio, authorization)

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


@router.get("/{portfolio_id}/performance/{symbol}", response_model=list[HoldingPricePointOut])
def get_portfolio_holding_performance(
    portfolio_id: int,
    symbol: str,
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
    authorization: Annotated[str | None, Header()] = None,
) -> list[dict]:
    """Same shape and semantics as GET /dashboard/performance/{symbol}, for
    any portfolio_id rather than just the caller's own — feeds the
    holding-detail chart when the ticker was reached from an agent
    portfolio's holdings list. Agent portfolios are publicly readable; a
    user portfolio requires the owning user's token."""
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    _require_owner_if_user_portfolio(db, portfolio, authorization)

    effective_since = since
    if effective_since is None and range_:
        now = datetime.utcnow()
        if range_ == "today":
            effective_since = datetime(now.year, now.month, now.day)
        elif range_ == "ytd":
            effective_since = datetime(now.year, 1, 1)
        else:
            effective_since = now - _RANGE_WINDOWS[range_]

    return get_symbol_price_history(db, portfolio.id, symbol, start=effective_since, end=until)


@router.get("/{portfolio_id}/trades", response_model=list[TradeOut])
def get_trade_history(
    portfolio_id: int,
    db: DbSession,
    symbol: str | None = Query(
        None, description="Filter to a single ticker (case-insensitive)."
    ),
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
    authorization: Annotated[str | None, Header()] = None,
) -> list[Trade]:
    """Trade history for a portfolio, newest first. 404s if the portfolio
    doesn't exist; returns [] if it exists but has no trades yet. Agent
    portfolios are publicly readable; a user portfolio requires the
    owning user's token (401 missing/invalid, 403 wrong user)."""
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    _require_owner_if_user_portfolio(db, portfolio, authorization)

    query = db.query(Trade).filter(Trade.portfolio_id == portfolio_id)
    if symbol:
        query = query.filter(Trade.symbol == symbol.upper())
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
