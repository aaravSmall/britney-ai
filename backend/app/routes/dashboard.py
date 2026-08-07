from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from app.deps import CurrentUser, DbSession
from app.models import PortfolioSnapshot
from app.schemas.dashboard import DashboardResponse, HoldingPricePointOut, SnapshotPointOut
from app.services.portfolio_service import build_dashboard, get_or_create_portfolio
from app.services.portfolio_snapshot_service import get_snapshots, get_symbol_price_history

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Shorthand -> lookback window for the `range` query param on /performance,
# matching the chart's range toggles (ChartRange in the Flutter app). "ytd"
# isn't a fixed duration so it's handled separately below.
_RANGE_WINDOWS: dict[str, timedelta] = {
    "today": timedelta(days=1),
    "week": timedelta(days=7),
    "month30": timedelta(days=30),
    "fiveYear": timedelta(days=365 * 5),
}


@router.get("", response_model=DashboardResponse)
async def get_dashboard(db: DbSession, user: CurrentUser) -> DashboardResponse:
    return await build_dashboard(db, user)


@router.get("/performance", response_model=list[SnapshotPointOut])
def get_performance(
    db: DbSession,
    user: CurrentUser,
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
    """Real PortfolioSnapshot history for the caller's own portfolio,
    oldest first — feeds the dashboard's performance chart. Returns an
    empty list if the agent hasn't captured any snapshots yet."""
    portfolio = get_or_create_portfolio(db, user)

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


@router.get("/performance/{symbol}", response_model=list[HoldingPricePointOut])
def get_holding_performance(
    symbol: str,
    db: DbSession,
    user: CurrentUser,
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
) -> list[dict]:
    """Same underlying PortfolioSnapshot rows as GET /dashboard/performance,
    for the caller's own portfolio, scoped to a single symbol — feeds the
    per-holding price chart on the stock/holding detail page."""
    portfolio = get_or_create_portfolio(db, user)

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
