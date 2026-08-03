"""Aggregate portfolio values and performance series for dashboard."""

from datetime import datetime, timedelta
from random import Random

from sqlalchemy.orm import Session

from app.models import PortfolioHolding, User
from app.schemas.dashboard import DashboardResponse, HoldingOut, PerformancePoint
from app.services import market_data


def ensure_demo_holdings(db: Session, user: User) -> None:
    """Seed a small mock portfolio once so the dashboard is meaningful."""
    if user.holdings:
        return
    seed = [
        ("VOO", "stock", 2.0, 450.0),
        ("AAPL", "stock", 1.5, 220.0),
        ("BTC", "crypto", 0.01, 95000.0),
    ]
    for sym, atype, qty, cost in seed:
        db.add(
            PortfolioHolding(
                user_id=user.id,
                symbol=sym,
                asset_type=atype,
                quantity=qty,
                avg_cost=cost,
            )
        )
    db.commit()
    # Reload relationship so the dashboard sees new rows
    db.expire(user)


async def build_dashboard(db: Session, user: User) -> DashboardResponse:
    ensure_demo_holdings(db, user)
    holdings_out: list[HoldingOut] = []
    total_mv = 0.0
    cash = 10_000.0  # mock cash; replace with ledger / broker later

    for h in user.holdings:
        price = await market_data.get_price_for_holding(h.symbol, h.asset_type)
        if price is None:
            price = h.last_price or h.avg_cost
        mv = h.quantity * price
        total_mv += mv
        holdings_out.append(
            HoldingOut(
                symbol=h.symbol,
                asset_type=h.asset_type,
                quantity=h.quantity,
                avg_cost=h.avg_cost,
                last_price=price,
                market_value=mv,
            )
        )

    # Mock performance: deterministic curve from user id
    rng = Random(user.id)
    points: list[PerformancePoint] = []
    base = max(total_mv + cash, 1000.0)
    for i in range(30, -1, -1):
        d = datetime.utcnow() - timedelta(days=i)
        jitter = 1.0 + (rng.random() - 0.48) * 0.02
        base *= jitter
        points.append(
            PerformancePoint(date=d.strftime("%Y-%m-%d"), value=round(base, 2))
        )

    day_change = None
    if len(points) >= 2:
        prev, last = points[-2].value, points[-1].value
        if prev:
            day_change = round((last - prev) / prev * 100, 2)

    return DashboardResponse(
        cash_balance=cash,
        total_portfolio_value=round(total_mv + cash, 2),
        day_change_pct=day_change,
        holdings=holdings_out,
        performance=points,
    )


def portfolio_summary_text(db: Session, user: User) -> str:
    lines = []
    for h in user.holdings:
        lines.append(
            f"- {h.symbol} ({h.asset_type}) qty {h.quantity} @ avg {h.avg_cost}"
        )
    if not lines:
        return "No holdings yet."
    return "\n".join(lines)
