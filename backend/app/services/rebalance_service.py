"""Target-allocation rebalance logic for the agent's three risk-tier
model portfolios — a separate, independent concern from
agent/decision_loop.py's news-driven buy/sell signals. decision_loop only
trades when a confidence-gated news signal fires, which can leave a tier
sitting on a large cash pile indefinitely if nothing clears the bar
(conservative's 0.75 threshold has real recent history of ~87% idle
cash — see the investigation that led to this module). This tops each
tier back up toward a fixed target weight on a schedule
(agent/run_rebalance.py), regardless of news.

Buy-only: an overweight position is never sold down here — selling to
rebalance is out of scope for now (see run_rebalance.py's module
docstring). Reuses the exact same execute_trade()/record_trade_fill()
path decision_loop.py and auto_invest_service.py already use, tagged
Trade.source="rebalance" so these fills are distinguishable in trade
history and — since no AgentDecision row is ever created for one, unlike
decision_loop's news-driven trades — never surface the AI trade-rationale
"More info" UI (GET .../trades/{id}/decision 404s on trade.agent_decision
being None, same as any other non-agent-sourced trade).

Crypto is deliberately absent from every tier's target weights: crypto
has never actually traded (the CryptoPanic news key was never
configured — see agent/news_ingestion.py's TARGET_PORTFOLIOS docstring),
and this job must not be the thing that first creates crypto exposure on
its own. Conservative has no crypto sleeve at all anymore (BTC removed
from TARGET_PORTFOLIOS); moderate/aggressive's existing ETH/SOL exposure
is left exactly as news-driven trading has produced it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models import Portfolio, Trade
from app.services import market_data
from app.services.portfolio_service import (
    InsufficientFundsError,
    build_portfolio_summary,
    record_trade_fill,
)
from app.trading.execution import execute_trade

# Target weight per stock/ETF ticker, by risk tier — whatever's left over
# is cash (conservative: 5%, moderate: 3%, aggressive: 2%). Moderate/
# aggressive split evenly across their existing non-crypto tickers.
REBALANCE_TARGET_WEIGHTS: dict[str, dict[str, float]] = {
    "conservative": {"VOO": 0.60, "BND": 0.35},
    "moderate": {"SPY": 0.485, "MSFT": 0.485},
    "aggressive": {"QQQ": 0.49, "AAPL": 0.49},
}

# An asset under its target by more than this many percentage points
# gets topped up; smaller gaps are left alone rather than chasing exact
# precision every run.
REBALANCE_TOLERANCE = 0.02

# A shortfall buy smaller than this is skipped as not meaningfully sized
# — no minimum-trade-size constant exists elsewhere in the codebase to
# reuse (checked), so this is a plain default per the task's instruction.
MIN_TRADE_DOLLARS = 10.0


@dataclass
class RebalancePlanItem:
    """One target asset's computed state for a portfolio, before any
    trade executes — skip_reason is None exactly when this item will be
    bought."""

    symbol: str
    current_weight: float
    target_weight: float
    shortfall_weight: float
    buy_dollars: float
    skip_reason: str | None


async def _pending_buy_notional(db: Session, portfolio: Portfolio) -> float:
    """Total current-value estimate of this portfolio's queued off-hours
    buy trades (Trade.status == "pending", see
    agent/decision_loop.py's _queue()/portfolio_service.queue_pending_trade)
    — cash already earmarked for a trade that hasn't filled yet, and
    which must not be double-counted as available for a rebalance buy.
    Pending rows store a 0.0 price placeholder (nothing to sum directly),
    so this re-fetches each symbol's current indicative price, the same
    estimate decision_loop._queue() used to size the order in the first
    place."""
    pending_buys = (
        db.query(Trade)
        .filter(
            Trade.portfolio_id == portfolio.id,
            Trade.status == "pending",
            Trade.side == "buy",
        )
        .all()
    )
    total = 0.0
    for trade in pending_buys:
        price = await market_data.get_price_for_holding(trade.symbol, trade.asset_type)
        if price is None:
            continue
        total += trade.quantity * price
    return total


async def plan_rebalance(db: Session, portfolio: Portfolio, tier: str) -> list[RebalancePlanItem]:
    """Pure computation, no side effects: current allocation vs.
    REBALANCE_TARGET_WEIGHTS[tier], with available cash already reduced
    by _pending_buy_notional(). Used both by rebalance_portfolio() (which
    executes the resulting buys) and by run_rebalance.py's --dry-run mode
    (which only logs it) — one code path computes the plan, so dry-run
    can never drift from what a real run would actually do.

    available_cash below MIN_TRADE_DOLLARS skips the whole portfolio
    (nothing meaningful is buyable regardless of allocation gaps); an
    individual asset's shortfall buy below MIN_TRADE_DOLLARS (whether
    because the gap is small or because an earlier item in this same
    plan already spent the rest of available_cash) skips just that
    asset, not the whole portfolio."""
    targets = REBALANCE_TARGET_WEIGHTS.get(tier, {})

    summary = await build_portfolio_summary(db, portfolio)
    pending_notional = await _pending_buy_notional(db, portfolio)
    available_cash = summary.cash_balance - pending_notional
    total_value = summary.total_portfolio_value
    current_value_by_symbol = {h.symbol: (h.market_value or 0.0) for h in summary.holdings}

    def _current_weight(symbol: str) -> float:
        if not total_value:
            return 0.0
        return current_value_by_symbol.get(symbol, 0.0) / total_value

    if available_cash < MIN_TRADE_DOLLARS:
        return [
            RebalancePlanItem(
                symbol=symbol,
                current_weight=_current_weight(symbol),
                target_weight=target_weight,
                shortfall_weight=target_weight - _current_weight(symbol),
                buy_dollars=0.0,
                skip_reason=(
                    f"portfolio available cash ${available_cash:,.2f} "
                    f"(cash_balance ${summary.cash_balance:,.2f} minus "
                    f"${pending_notional:,.2f} earmarked for pending trades) "
                    f"is below the ${MIN_TRADE_DOLLARS:,.2f} minimum"
                ),
            )
            for symbol, target_weight in targets.items()
        ]

    plan: list[RebalancePlanItem] = []
    remaining_cash = available_cash
    for symbol, target_weight in targets.items():
        current_weight = _current_weight(symbol)
        shortfall_weight = target_weight - current_weight

        if shortfall_weight <= REBALANCE_TOLERANCE:
            plan.append(
                RebalancePlanItem(
                    symbol, current_weight, target_weight, shortfall_weight, 0.0,
                    "within tolerance",
                )
            )
            continue

        buy_dollars = min(shortfall_weight * total_value, remaining_cash)
        if buy_dollars < MIN_TRADE_DOLLARS:
            plan.append(
                RebalancePlanItem(
                    symbol, current_weight, target_weight, shortfall_weight, 0.0,
                    f"buy amount ${buy_dollars:,.2f} is below the "
                    f"${MIN_TRADE_DOLLARS:,.2f} minimum",
                )
            )
            continue

        plan.append(
            RebalancePlanItem(
                symbol, current_weight, target_weight, shortfall_weight, buy_dollars, None
            )
        )
        remaining_cash -= buy_dollars

    return plan


async def rebalance_portfolio(db: Session, portfolio: Portfolio, tier: str) -> list[Trade]:
    """Executes plan_rebalance()'s buys (items with skip_reason is None)
    through the same execute_trade()/record_trade_fill() path
    decision_loop.py and auto_invest_service.py already use, tagged
    source="rebalance". Returns the Trade rows actually filled — empty
    if nothing needed buying or available cash didn't allow it."""
    plan = await plan_rebalance(db, portfolio, tier)

    trades: list[Trade] = []
    for item in plan:
        if item.skip_reason is not None:
            continue

        price = await market_data.get_price_for_holding(item.symbol, "stock")
        if price is None:
            continue
        quantity = round(item.buy_dollars / price, 6)
        if quantity <= 0:
            continue

        result = execute_trade(item.symbol, "stock", "buy", quantity, simulate_only=True)
        if result.status != "filled":
            continue

        try:
            trade = record_trade_fill(
                db,
                portfolio,
                item.symbol,
                "stock",
                "buy",
                quantity,
                price,
                simulated=result.simulated,
                order_id=result.order_id,
                source="rebalance",
            )
        except InsufficientFundsError:
            # Cash moved between plan_rebalance()'s snapshot and this
            # fill (e.g. an earlier item in this same plan spent more
            # than expected due to price drift) — skip this one, next
            # day's run re-evaluates from scratch.
            continue

        trades.append(trade)

    return trades
