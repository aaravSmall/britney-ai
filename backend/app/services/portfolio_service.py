"""Aggregate portfolio values and performance series for dashboard."""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import Portfolio, PortfolioHolding, PortfolioSnapshot, Trade, User
from app.schemas.dashboard import DashboardResponse, HoldingOut
from app.services import market_data
from app.services.cash_ledger import apply_cash_delta


class InsufficientFundsError(Exception):
    pass


class InsufficientHoldingsError(Exception):
    pass


def get_or_create_portfolio(db: Session, user: User) -> Portfolio:
    """Every user gets exactly one portfolio, created lazily on first use
    (mirrors how ensure_demo_holdings used to lazily seed holdings)."""
    if user.portfolio is not None:
        return user.portfolio
    portfolio = Portfolio(user_id=user.id)
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


def record_trade_fill(
    db: Session,
    portfolio: Portfolio,
    symbol: str,
    asset_type: str,
    side: str,
    quantity: float,
    price: float,
    *,
    simulated: bool = True,
    order_id: str | None = None,
    source: str = "user",
) -> Trade:
    """Apply a filled paper trade to the portfolio's cash/holdings and log
    it as a Trade row. Callers resolve the Portfolio first (via
    get_or_create_portfolio for a User, or a direct lookup for the agent's
    scheduled runs) — this function no longer needs a User at all, so it
    works identically whether the caller is an authenticated HTTP request
    or an unattended agent loop. `source` distinguishes user-initiated
    fills from the autonomous agent's."""
    holding = next(
        (
            h
            for h in portfolio.holdings
            if h.symbol == symbol and h.asset_type == asset_type
        ),
        None,
    )
    cost = quantity * price

    if side == "buy" and portfolio.cash_balance < cost:
        raise InsufficientFundsError(
            f"Insufficient cash: need ${cost:,.2f}, have ${portfolio.cash_balance:,.2f}"
        )
    if side == "sell" and (not holding or holding.quantity < quantity):
        have = holding.quantity if holding else 0.0
        raise InsufficientHoldingsError(
            f"Insufficient {symbol}: trying to sell {quantity}, hold {have}"
        )

    trade = Trade(
        portfolio_id=portfolio.id,
        symbol=symbol,
        asset_type=asset_type,
        side=side,
        quantity=quantity,
        price=price,
        status="filled",
        simulated=simulated,
        source=source,
        order_id=order_id,
    )
    db.add(trade)
    db.flush()  # assigns trade.id, needed below by the cash ledger entry

    apply_cash_delta(
        db,
        portfolio,
        -cost if side == "buy" else cost,
        "trade",
        trade_id=trade.id,
        note=f"{side.upper()} {quantity} {symbol} @ ${price:,.2f}",
    )

    if side == "buy":
        if holding:
            new_qty = holding.quantity + quantity
            holding.avg_cost = (
                holding.avg_cost * holding.quantity + cost
            ) / new_qty
            holding.quantity = new_qty
            holding.last_price = price
        else:
            holding = PortfolioHolding(
                portfolio_id=portfolio.id,
                symbol=symbol,
                asset_type=asset_type,
                quantity=quantity,
                avg_cost=price,
                last_price=price,
            )
            db.add(holding)
    else:  # sell
        holding.quantity -= quantity
        holding.last_price = price
        if holding.quantity <= 1e-9:
            db.delete(holding)

    db.commit()
    db.refresh(portfolio)
    db.refresh(trade)
    return trade


def ensure_demo_holdings(db: Session, user: User) -> Portfolio:
    """Seed a small mock portfolio once so the dashboard is meaningful."""
    portfolio = get_or_create_portfolio(db, user)
    if portfolio.holdings:
        return portfolio
    seed = [
        ("VOO", "stock", 2.0, 450.0),
        ("AAPL", "stock", 1.5, 220.0),
        ("BTC", "crypto", 0.01, 95000.0),
    ]
    for sym, atype, qty, cost in seed:
        db.add(
            PortfolioHolding(
                portfolio_id=portfolio.id,
                symbol=sym,
                asset_type=atype,
                quantity=qty,
                avg_cost=cost,
            )
        )
    db.commit()
    # Reload relationship so the dashboard sees new rows
    db.expire(portfolio)
    return portfolio


async def build_dashboard(db: Session, user: User) -> DashboardResponse:
    portfolio = ensure_demo_holdings(db, user)
    return await build_portfolio_summary(db, portfolio)


async def build_portfolio_summary(db: Session, portfolio: Portfolio) -> DashboardResponse:
    """Portfolio-generic core of the dashboard: holdings valuation and
    day-change, for any Portfolio row (a real user's or one of the agent's
    three model portfolios). Unlike build_dashboard(), this never seeds
    demo holdings — that's specifically for a fresh human login, and would
    misrepresent an agent portfolio's real (possibly empty) trading
    history if applied here (see ensure_target_portfolios' docstring)."""
    holdings_out: list[HoldingOut] = []
    total_mv = 0.0
    cash = portfolio.cash_balance

    for h in portfolio.holdings:
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

    total_value = total_mv + cash
    day_change = _day_change_pct(db, portfolio.id, total_value)

    return DashboardResponse(
        portfolio_id=portfolio.id,
        cash_balance=cash,
        total_portfolio_value=round(total_value, 2),
        day_change_pct=day_change,
        holdings=holdings_out,
    )


def _day_change_pct(
    db: Session, portfolio_id: int, current_total: float
) -> float | None:
    """% change vs. the most recent real PortfolioSnapshot at least 24h
    old. None until the agent has been snapshotting this portfolio for a
    full day — there's no fake fallback series to fall back on anymore."""
    cutoff = datetime.utcnow() - timedelta(hours=24)
    day_ago = (
        db.query(PortfolioSnapshot)
        .filter(
            PortfolioSnapshot.portfolio_id == portfolio_id,
            PortfolioSnapshot.timestamp <= cutoff,
        )
        .order_by(PortfolioSnapshot.timestamp.desc())
        .first()
    )
    if day_ago is None or not day_ago.total_value:
        return None
    return round((current_total - day_ago.total_value) / day_ago.total_value * 100, 2)


def portfolio_summary_text(db: Session, user: User) -> str:
    portfolio = get_or_create_portfolio(db, user)
    lines = []
    for h in portfolio.holdings:
        lines.append(
            f"- {h.symbol} ({h.asset_type}) qty {h.quantity} @ avg {h.avg_cost}"
        )
    if not lines:
        return "No holdings yet."
    return "\n".join(lines)
