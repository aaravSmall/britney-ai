"""Due-check and execution logic for AutoInvestSchedule rows, shared by
backend/agent/run_auto_invest.py (the standalone polling loop) and the
test suite. Executes through the exact same execute_trade/
record_trade_fill path routes/trading.py and agent/decision_loop.py
already use — no separate trade-execution logic here.
"""

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import AutoInvestSchedule, Portfolio, Trade
from app.services import market_data
from app.services.portfolio_service import record_trade_fill
from app.trading.execution import execute_trade
from app.trading.sizing import floor_to_6dp

# Simple elapsed-time thresholds, not calendar-aware (a "monthly"
# schedule fires every ~30 days, not on a fixed day-of-month).
INTERVAL_THRESHOLDS: dict[str, timedelta] = {
    "daily": timedelta(hours=24),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
}


def is_due(schedule: AutoInvestSchedule, now: datetime | None = None) -> bool:
    """A disabled schedule is never due. A schedule that has never run is
    always due. Otherwise due once its interval's threshold has elapsed
    since last_executed_at."""
    if not schedule.enabled:
        return False
    if schedule.last_executed_at is None:
        return True
    now = now or datetime.utcnow()
    return now - schedule.last_executed_at >= INTERVAL_THRESHOLDS[schedule.interval]


async def execute_schedule(db: Session, schedule: AutoInvestSchedule) -> Trade:
    """Buys `schedule.amount` dollars of `schedule.ticker` against the
    schedule's portfolio and marks it executed. Raises (ValueError if no
    price is available, InsufficientFundsError from record_trade_fill if
    the portfolio can't afford it) without updating last_executed_at —
    callers must not treat a raised exception as a partial success."""
    portfolio = db.get(Portfolio, schedule.portfolio_id)
    price = await market_data.get_price_for_holding(schedule.ticker, schedule.asset_type)
    if price is None:
        raise ValueError(f"No price available for {schedule.ticker}")

    # Floor, not round() — rounding up can make quantity*price exceed
    # schedule.amount by a fraction of a cent, which trips
    # InsufficientFundsError when cash_balance sits close to
    # schedule.amount (the same production mechanism fixed in
    # rebalance_service.py — see app.trading.sizing.floor_to_6dp).
    quantity = floor_to_6dp(schedule.amount / price)
    if quantity <= 0:
        raise ValueError(f"Computed buy quantity for {schedule.ticker} was zero.")

    result = execute_trade(schedule.ticker, schedule.asset_type, "buy", quantity, simulate_only=True)
    if result.status != "filled":
        raise ValueError(f"Execution did not fill: {result.message}")

    trade = record_trade_fill(
        db,
        portfolio,
        schedule.ticker,
        schedule.asset_type,
        "buy",
        quantity,
        price,
        simulated=result.simulated,
        order_id=result.order_id,
        source="auto_invest",
    )

    schedule.last_executed_at = datetime.utcnow()
    db.add(schedule)
    db.commit()
    return trade
