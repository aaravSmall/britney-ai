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
docstring). This now extends to obvious/non-obvious classification too
(see below): a ticker that stops passing classification simply stops
receiving new buys, it is never sold. Reuses the exact same
execute_trade()/record_trade_fill() path decision_loop.py and
auto_invest_service.py already use, tagged Trade.source="rebalance" so
these fills are distinguishable in trade history and — since no
AgentDecision row is ever created for one, unlike decision_loop's
news-driven trades — never surface the AI trade-rationale "More info" UI
(GET .../trades/{id}/decision 404s on trade.agent_decision being None,
same as any other non-agent-sourced trade).

Crypto is deliberately absent from every tier's target weights: crypto
has never actually traded (the CryptoPanic news key was never
configured — see agent/news_ingestion.py's TARGET_PORTFOLIOS docstring),
and this job must not be the thing that first creates crypto exposure on
its own. Conservative has no crypto sleeve at all anymore (BTC removed
from TARGET_PORTFOLIOS); moderate/aggressive's existing ETH/SOL exposure
is left exactly as news-driven trading has produced it. Discovery
(agent/discovery.py) never validates crypto tickers either (its
validation gates only call stock_data.quote(), a stocks-only path), so
the non-obvious bucket below can never contain crypto either.

--- Obvious/non-obvious split (see docs/DISCOVERY_DESIGN.md §5) ---

REBALANCE_TARGET_WEIGHTS is now nested per tier: {"obvious": {...},
"non_obvious": {...}}. "obvious" holds the STATIC base weights for the 6
fixed target tickers (unchanged day to day — only whether a given ticker
currently PASSES classification changes, gating whether it receives a
buy this cycle, not its weight). "non_obvious" is deliberately left as an
empty placeholder here: which tickers occupy it, and at what weight,
changes every day based on agent/classification.py's routing output, so
it cannot be a static module constant — see effective_target_weights()
below, which builds the real per-cycle flat targets dict every
run_rebalance.py cycle actually uses.

Cash targets are unchanged from before this split existed (conservative
5%, moderate 3%, aggressive 2% — see TIER_CASH_WEIGHT). The obvious/
non-obvious split applies to each tier's remaining INVESTED portion, per
agent_config.SPLIT_RATIOS (conservative 90/10, moderate 75/25, aggressive
55/45 — see that constant's docstring for the reasoning and its
"known-provisional" flag).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from agent import agent_config
from app.models import Portfolio, Trade
from app.services import market_data
from app.services.portfolio_service import (
    InsufficientFundsError,
    build_portfolio_summary,
    record_trade_fill,
)
from app.trading.execution import execute_trade

# Static base weight per fixed stock/ETF ticker, by risk tier, WITHIN
# that tier's "obvious" bucket — see this module's docstring for why
# "non_obvious" is an empty placeholder here rather than populated
# statically. These numbers are each tier's OLD flat target (e.g.
# conservative's original VOO 0.60 / BND 0.35, in their original 60:35
# relative proportion) rescaled to fit inside the new smaller "obvious"
# envelope: (1 - TIER_CASH_WEIGHT[tier]) * SPLIT_RATIOS[tier]["obvious"],
# split across the tier's fixed tickers in their original relative
# proportions (moderate/aggressive were already an even split, so their
# envelope is just divided by 2).
#   conservative: envelope = 0.95 * 0.90 = 0.855, split 60:35 -> 0.540 / 0.315
#   moderate:     envelope = 0.97 * 0.75 = 0.7275, split evenly -> 0.36375 each
#   aggressive:   envelope = 0.98 * 0.55 = 0.539, split evenly -> 0.2695 each
REBALANCE_TARGET_WEIGHTS: dict[str, dict[str, dict[str, float]]] = {
    "conservative": {
        "obvious": {"VOO": 0.540, "BND": 0.315},
        "non_obvious": {},  # populated per-cycle — see effective_target_weights()
    },
    "moderate": {
        "obvious": {"SPY": 0.36375, "MSFT": 0.36375},
        "non_obvious": {},
    },
    "aggressive": {
        "obvious": {"QQQ": 0.2695, "AAPL": 0.2695},
        "non_obvious": {},
    },
}

# Unchanged from before the obvious/non-obvious split existed — the
# portion of each tier that's never targeted by this job at all, whether
# obvious or non-obvious.
TIER_CASH_WEIGHT: dict[str, float] = {
    "conservative": 0.05,
    "moderate": 0.03,
    "aggressive": 0.02,
}

# An asset under its target by more than this many percentage points
# gets topped up; smaller gaps are left alone rather than chasing exact
# precision every run.
REBALANCE_TOLERANCE = 0.02

# A shortfall buy smaller than this is skipped as not meaningfully sized
# — no minimum-trade-size constant exists elsewhere in the codebase to
# reuse (checked), so this is a plain default per the task's instruction.
MIN_TRADE_DOLLARS = 10.0


def effective_target_weights(
    tier: str,
    *,
    obvious_pass: set[str] | None = None,
    non_obvious_routed: list[tuple[str, float]] | None = None,
) -> dict[str, float]:
    """Builds the flat {ticker: target_weight} dict plan_rebalance()
    actually buys toward THIS cycle, from the nested static
    REBALANCE_TARGET_WEIGHTS[tier] plus today's classification output.

    obvious_pass: tickers (from REBALANCE_TARGET_WEIGHTS[tier]["obvious"])
    that currently pass classification. A fixed ticker NOT in this set is
    simply omitted from the returned dict — plan_rebalance()'s shortfall
    loop then never generates a buy for it, and since this job never
    sells (see module docstring), an existing holding in it is just left
    untouched. Defaults to "every fixed ticker for this tier" (i.e. every
    key already in the static "obvious" sub-dict) when None, which
    reproduces this job's exact pre-classification behavior — the safe
    fallback for a cycle where agent/classification.py hasn't run yet
    (e.g. very first-ever rebalance) or wasn't passed in.

    non_obvious_routed: (ticker, confidence) pairs already routed to this
    tier by agent/classification.route_non_obvious() — NOT yet capped or
    weighted. This function applies agent_config.MAX_CONCURRENT_NON_OBVIOUS,
    keeping the highest-confidence candidates up to that cap, and
    equal-weights the survivors across the tier's non-obvious envelope
    ((1 - TIER_CASH_WEIGHT[tier]) * SPLIT_RATIOS[tier]["non_obvious"]).
    Defaults to "nothing routed" when None or empty — per
    docs/DISCOVERY_DESIGN.md §6, a cycle with zero qualifying non-obvious
    candidates leaves that envelope's cash simply un-invested (this
    function just omits it from the returned dict entirely), it never
    falls back to buying more of the obvious bucket with it.
    """
    base = REBALANCE_TARGET_WEIGHTS[tier]
    obvious_base = base["obvious"]
    if obvious_pass is None:
        obvious_pass = set(obvious_base)

    targets: dict[str, float] = {
        ticker: weight for ticker, weight in obvious_base.items() if ticker in obvious_pass
    }

    if non_obvious_routed:
        cap = agent_config.MAX_CONCURRENT_NON_OBVIOUS[tier]
        selected = sorted(non_obvious_routed, key=lambda pair: pair[1], reverse=True)[:cap]
        if selected:
            non_obvious_envelope = (
                1 - TIER_CASH_WEIGHT[tier]
            ) * agent_config.SPLIT_RATIOS[tier]["non_obvious"]
            per_ticker_weight = non_obvious_envelope / len(selected)
            for ticker, _confidence in selected:
                targets[ticker] = per_ticker_weight

    return targets


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


async def plan_rebalance(
    db: Session,
    portfolio: Portfolio,
    tier: str,
    *,
    obvious_pass: set[str] | None = None,
    non_obvious_routed: list[tuple[str, float]] | None = None,
) -> list[RebalancePlanItem]:
    """Pure computation, no side effects: current allocation vs.
    effective_target_weights(tier, ...) (obvious tickers that currently
    pass classification, plus this cycle's routed+capped+equal-weighted
    non-obvious tickers — see that function's docstring for the
    obvious_pass/non_obvious_routed defaults), with available cash
    already reduced by _pending_buy_notional(). Used both by
    rebalance_portfolio() (which executes the resulting buys) and by
    run_rebalance.py's --dry-run mode (which only logs it) — one code
    path computes the plan, so dry-run can never drift from what a real
    run would actually do.

    available_cash below MIN_TRADE_DOLLARS skips the whole portfolio
    (nothing meaningful is buyable regardless of allocation gaps); an
    individual asset's shortfall buy below MIN_TRADE_DOLLARS (whether
    because the gap is small or because an earlier item in this same
    plan already spent the rest of available_cash) skips just that
    asset, not the whole portfolio."""
    targets = effective_target_weights(
        tier, obvious_pass=obvious_pass, non_obvious_routed=non_obvious_routed
    )

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


async def rebalance_portfolio(
    db: Session,
    portfolio: Portfolio,
    tier: str,
    *,
    obvious_pass: set[str] | None = None,
    non_obvious_routed: list[tuple[str, float]] | None = None,
) -> list[Trade]:
    """Executes plan_rebalance()'s buys (items with skip_reason is None)
    through the same execute_trade()/record_trade_fill() path
    decision_loop.py and auto_invest_service.py already use, tagged
    source="rebalance" — same tag for both obvious and non-obvious buys,
    not split further; distinguishing them in trade history if needed
    later can read effective_target_weights()'s two source dicts, no
    schema change required for that today. Returns the Trade rows
    actually filled — empty if nothing needed buying or available cash
    didn't allow it. See obvious_pass/non_obvious_routed on
    plan_rebalance() for what these parameters do and their defaults."""
    plan = await plan_rebalance(
        db, portfolio, tier, obvious_pass=obvious_pass, non_obvious_routed=non_obvious_routed
    )

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
