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


def _settle_fill(db: Session, portfolio: Portfolio, trade: Trade, price: float) -> None:
    """Cash/holdings/ledger mutation for one fill — the one place that
    happens, shared by record_trade_fill (new trade, filled immediately)
    and fill_pending_trade (an existing queued Trade row, filled later at
    the actual market-open price). Mutates `trade.price`/`trade.status` in
    place rather than creating a row — the caller owns creating/looking up
    `trade` and committing."""
    holding = next(
        (
            h
            for h in portfolio.holdings
            if h.symbol == trade.symbol and h.asset_type == trade.asset_type
        ),
        None,
    )
    cost = trade.quantity * price

    if trade.side == "buy" and portfolio.cash_balance < cost:
        raise InsufficientFundsError(
            f"Insufficient cash: need ${cost:,.2f}, have ${portfolio.cash_balance:,.2f}"
        )
    if trade.side == "sell" and (not holding or holding.quantity < trade.quantity):
        have = holding.quantity if holding else 0.0
        raise InsufficientHoldingsError(
            f"Insufficient {trade.symbol}: trying to sell {trade.quantity}, hold {have}"
        )

    trade.price = price
    trade.status = "filled"

    apply_cash_delta(
        db,
        portfolio,
        -cost if trade.side == "buy" else cost,
        "trade",
        trade_id=trade.id,
        note=f"{trade.side.upper()} {trade.quantity} {trade.symbol} @ ${price:,.2f}",
    )

    if trade.side == "buy":
        if holding:
            new_qty = holding.quantity + trade.quantity
            holding.avg_cost = (
                holding.avg_cost * holding.quantity + cost
            ) / new_qty
            holding.quantity = new_qty
            holding.last_price = price
        else:
            holding = PortfolioHolding(
                portfolio_id=portfolio.id,
                symbol=trade.symbol,
                asset_type=trade.asset_type,
                quantity=trade.quantity,
                avg_cost=price,
                last_price=price,
            )
            db.add(holding)
    else:  # sell
        holding.quantity -= trade.quantity
        holding.last_price = price
        if holding.quantity <= 1e-9:
            db.delete(holding)


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

    _settle_fill(db, portfolio, trade, price)

    db.commit()
    db.refresh(portfolio)
    db.refresh(trade)
    return trade


def queue_pending_trade(
    db: Session,
    portfolio: Portfolio,
    symbol: str,
    asset_type: str,
    side: str,
    quantity: float,
    scheduled_execution_time: datetime,
    *,
    source: str = "agent",
) -> Trade:
    """Writes a queued off-hours stock Trade — no cash/holdings/ledger
    effect at all until fill_pending_trade() actually settles it at the
    real market-open price. `price` is stored as a 0.0 placeholder (Trade.
    price is NOT NULL at the DB level in both SQLite and Postgres, and
    bootstrap_schema()'s additive-only ALTER TABLE ADD COLUMN can't safely
    relax that across both dialects) — callers must never read a
    status="pending" row's price as real; the API layer
    (schemas/trading.py's TradeOut) nulls it out for exactly this reason.
    """
    trade = Trade(
        portfolio_id=portfolio.id,
        symbol=symbol,
        asset_type=asset_type,
        side=side,
        quantity=quantity,
        price=0.0,
        status="pending",
        simulated=True,
        source=source,
        order_id=None,
        scheduled_execution_time=scheduled_execution_time,
    )
    db.add(trade)
    db.commit()
    db.refresh(trade)
    return trade


def fill_pending_trade(db: Session, portfolio: Portfolio, trade: Trade, price: float) -> Trade:
    """Settles a previously-queued pending Trade at `price` (the actual
    market-open quote) — mutates the existing row in place (price/status,
    and timestamp to the real fill moment) instead of creating a new one.
    First cash/holdings effect this trade has ever had; raises
    InsufficientFundsError/InsufficientHoldingsError same as
    record_trade_fill if the portfolio can no longer afford/hold it by
    the time it fills, leaving the row untouched (still "pending") for the
    caller to log and retry later rather than silently dropping it. Sets
    trade.timestamp only after _settle_fill succeeds — if it raises, the
    row (and its timestamp) stay exactly as queued, since this trade and
    any other pending trade being filled in the same batch share one DB
    session/transaction (see agent.decision_loop.fill_due_pending_trades)."""
    _settle_fill(db, portfolio, trade, price)
    trade.timestamp = datetime.utcnow()
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
