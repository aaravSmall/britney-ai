"""Target-allocation rebalance logic for the agent's three risk-tier
model portfolios — a separate, independent concern from
agent/decision_loop.py's news-driven buy/sell signals. decision_loop only
trades when a confidence-gated news signal fires, which can leave a tier
sitting on a large cash pile indefinitely if nothing clears the bar
(conservative's 0.75 threshold has real recent history of ~87% idle
cash — see the investigation that led to this module). This tops each
tier back up toward a fixed target weight on a schedule
(agent/run_rebalance.py), regardless of news.

Mostly buy-only, with two DELIBERATE, confirmed exceptions (this reverses
the original "never sells" rule for these two cases specifically, it is
not an inconsistency):

  1. A fixed-6 ticker's confirmed (3-consecutive-day debounced) flip from
     obvious to non-obvious sells its full position — see
     sell_full_position() below and agent/classification.py's
     update_streaks(), wired together in agent/run_rebalance.py's cycle.
     Its weight then redistributes proportionally among that tier's
     remaining passing obvious tickers (see effective_target_weights()),
     rather than sitting as idle cash the way a merely-excluded ticker's
     weight used to.
  2. An emergency stop-loss (agent/stop_loss.py) sells ANY current
     position — obvious, non-obvious, or crypto — on a sharp drop,
     bypassing the classification debounce entirely. Tagged
     Trade.source="stop_loss", distinct from a classification-driven
     sell's Trade.source="rebalance", so the two are identifiable
     separately in trade history/rationale UI.

Every other overweight/underperforming position is still never sold —
rebalancing an overweight obvious-and-passing position down, for
instance, remains out of scope. Reuses the exact same
execute_trade()/record_trade_fill() path decision_loop.py and
auto_invest_service.py already use for both buys and these two sell
paths — since no AgentDecision row is ever created for one, unlike
decision_loop's news-driven trades, none of these ever surface the AI
trade-rationale "More info" UI (GET .../trades/{id}/decision 404s on
trade.agent_decision being None, same as any other non-agent-sourced
trade).

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

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from agent import agent_config
from app.models import Portfolio, Trade
from app.services import market_data
from app.services.portfolio_service import (
    InsufficientFundsError,
    InsufficientHoldingsError,
    build_portfolio_summary,
    record_trade_fill,
)
from app.trading.execution import execute_trade
from app.trading.sizing import floor_to_6dp

logger = logging.getLogger(__name__)

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
    global_obvious_discovered: set[str] | None = None,
    non_obvious_routed: list[tuple[str, float]] | None = None,
) -> dict[str, float]:
    """Builds the flat {ticker: target_weight} dict plan_rebalance()
    actually buys toward THIS cycle, from the nested static
    REBALANCE_TARGET_WEIGHTS[tier] plus today's classification output.

    obvious_pass: tickers (from REBALANCE_TARGET_WEIGHTS[tier]["obvious"])
    that currently pass classification (i.e. agent/classification.py's
    debounced EFFECTIVE status, not the raw daily signal — see
    update_streaks()). A fixed ticker NOT in this set is omitted from the
    returned dict, AND its static base weight is redistributed
    proportionally across the tier's full obvious pool (see below) —
    this redistribution is what "sell it, don't just stop buying it"
    actually means at the allocation-math level: the freed weight doesn't
    sit idle, it goes to what's left. Defaults to "every fixed ticker for
    this tier" when None — the safe fallback for a cycle where
    agent/classification.py hasn't run yet or wasn't passed in.

    global_obvious_discovered: discovered tickers that have CONFIRMED
    obvious status (agent/classification.global_obvious_pool() —
    debounced, 3 consecutive days, same as obvious_pass) — folded into
    EVERY tier's obvious bucket alongside its own fixed tickers, unlike
    non_obvious_routed below (which is volatility-routed to exactly one
    tier). This is deliberate: a discovered ticker that clears Gate C's
    strict volatility bar (agent_config.VOLATILITY_MAX_VS_VOO) has
    already proven itself "calm enough for any risk level," so there's no
    reason to gatekeep it to a single tier the way a merely-volatile
    non-obvious candidate is.

    Each tier's full obvious pool = (obvious_pass ∩ this tier's fixed
    tickers) ∪ global_obvious_discovered. Redistribution treats every
    member of that pool proportionally to a NOMINAL weight: a fixed
    ticker's nominal weight is its real static base weight (from
    REBALANCE_TARGET_WEIGHTS), unchanged; a global discovered ticker's
    nominal weight is this tier's AVERAGE fixed base weight (total
    envelope / this tier's ORIGINAL fixed-ticker count — a stable
    constant, not shrinking as the pool grows, so a 2nd or 3rd newcomer
    is weighted the same "average fixed slice," not a moving target).
    This choice matters concretely: it means today's real state (zero
    discovered tickers currently qualify) reproduces the exact original
    fixed-6 weights unchanged — this function's behavior is *exactly*
    backward-compatible when global_obvious_discovered is empty — while
    still giving a newly-qualifying ticker a fair, non-arbitrary starting
    weight once one does qualify, rather than either ignoring it or
    disrupting the fixed tickers' own mutual ratio (e.g. VOO:BND stays
    ~63:37 between themselves; a newcomer just adds a third, fairly-sized
    claim on the same envelope).

    If the tier's full obvious pool is empty (every fixed ticker fails
    AND nothing has been globally confirmed obvious), the whole envelope
    has nowhere to go and is simply omitted — same as the non-obvious
    zero-candidates case below, logged loudly by the caller
    (agent/run_rebalance.py), not silently absorbed here.

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
    global_obvious_discovered = global_obvious_discovered or set()

    total_obvious_envelope = sum(obvious_base.values())
    # Stable per-tier constant — this tier's ORIGINAL fixed-ticker count,
    # not the current pool size, so a 2nd/3rd newcomer doesn't shift what
    # "one average fixed slice" means for tickers already in the pool.
    newcomer_nominal_weight = (
        total_obvious_envelope / len(obvious_base) if obvious_base else 0.0
    )

    pool = (obvious_pass & set(obvious_base)) | global_obvious_discovered
    # Deterministic insertion order matters here: plan_rebalance() clamps
    # each item against whatever cash is LEFT after the ones before it in
    # iteration order (see plan_rebalance()'s remaining_cash accounting),
    # so an unstable order (e.g. iterating a set/dict-from-set directly)
    # would make WHICH ticker gets clamped first vary run to run. Fixed
    # tickers keep their original REBALANCE_TARGET_WEIGHTS order; any
    # global-obvious newcomers are appended after, sorted for determinism.
    nominal_weight_by_ticker: dict[str, float] = {}
    for ticker in obvious_base:
        if ticker in pool:
            nominal_weight_by_ticker[ticker] = obvious_base[ticker]
    for ticker in sorted(pool - set(obvious_base)):
        nominal_weight_by_ticker[ticker] = newcomer_nominal_weight
    passing_nominal_sum = sum(nominal_weight_by_ticker.values())

    targets: dict[str, float] = {}
    if passing_nominal_sum > 0:
        # Proportional redistribution: the pool splits the FULL envelope
        # by each member's nominal weight relative to the others' — e.g.
        # conservative with only VOO passing (BND excluded, nothing
        # globally obvious) gives VOO the entire 0.855 envelope, not just
        # VOO's own original 0.540; a newly-qualifying global ticker
        # joins that same split using its own nominal weight above.
        for ticker, nominal_weight in nominal_weight_by_ticker.items():
            targets[ticker] = nominal_weight / passing_nominal_sum * total_obvious_envelope
    # else: the tier's full obvious pool is empty — the whole envelope
    # has nowhere to redistribute to, so it's simply omitted (left as
    # cash). Deliberately not logged here (see docstring) — this is a
    # pure function; the caller logs it loudly.

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
    global_obvious_discovered: set[str] | None = None,
    non_obvious_routed: list[tuple[str, float]] | None = None,
) -> list[RebalancePlanItem]:
    """Pure computation, no side effects: current allocation vs.
    effective_target_weights(tier, ...) (obvious tickers that currently
    pass classification, PLUS any discovered tickers confirmed obvious
    globally, plus this cycle's routed+capped+equal-weighted non-obvious
    tickers — see that function's docstring for what each parameter does
    and its default), with available cash already reduced by
    _pending_buy_notional(). Used both by rebalance_portfolio() (which
    executes the resulting buys) and by run_rebalance.py's --dry-run mode
    (which only logs it) — one code path computes the plan, so dry-run
    can never drift from what a real run would actually do.

    available_cash below MIN_TRADE_DOLLARS skips the whole portfolio
    (nothing meaningful is buyable regardless of allocation gaps); an
    individual asset's shortfall buy below MIN_TRADE_DOLLARS (whether
    because the gap is small or because an earlier item in this same
    plan already spent the rest of available_cash) skips just that
    asset, not the whole portfolio."""
    targets = effective_target_weights(
        tier,
        obvious_pass=obvious_pass,
        global_obvious_discovered=global_obvious_discovered,
        non_obvious_routed=non_obvious_routed,
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
    global_obvious_discovered: set[str] | None = None,
    non_obvious_routed: list[tuple[str, float]] | None = None,
) -> list[Trade]:
    """Executes plan_rebalance()'s buys (items with skip_reason is None)
    through the same execute_trade()/record_trade_fill() path
    decision_loop.py and auto_invest_service.py already use, tagged
    source="rebalance" — same tag for every buy this function makes
    (obvious fixed, global-obvious discovered, or non-obvious), not split
    further; distinguishing them in trade history if needed later can
    read effective_target_weights()'s source dicts, no schema change
    required for that today. Returns the Trade rows actually filled —
    empty if nothing needed buying or available cash didn't allow it. See
    obvious_pass/global_obvious_discovered/non_obvious_routed on
    plan_rebalance() for what these parameters do and their defaults."""
    plan = await plan_rebalance(
        db, portfolio, tier,
        obvious_pass=obvious_pass,
        global_obvious_discovered=global_obvious_discovered,
        non_obvious_routed=non_obvious_routed,
    )

    trades: list[Trade] = []
    for item in plan:
        if item.skip_reason is not None:
            continue

        price = await market_data.get_price_for_holding(item.symbol, "stock")
        if price is None:
            continue
        # Floor to 6 decimals, not round() — rounding UP can make
        # quantity*price exceed item.buy_dollars by a fraction of a cent.
        # That's harmless when buy_dollars is well under available cash,
        # but plan_rebalance() clamps buy_dollars to EXACTLY the tier's
        # remaining cash whenever that's the binding constraint (routine
        # for a non-obvious envelope buy, which is often the last/only
        # item competing for 100% of what's left) — a real run hit
        # exactly this: quantity=1.138022 (round()) * price=$226.383 =
        # $257.628834, a hair above a cash_balance of $257.6287746,
        # tripping _settle_fill()'s InsufficientFundsError on a "fully
        # funded" buy. Flooring instead of rounding guarantees
        # quantity*price <= buy_dollars always, for every buy, not just
        # cash-clamped ones. See app.trading.sizing.floor_to_6dp — same
        # shared helper now used by auto_invest_service.py and
        # agent/decision_loop.py's own buy/sell sizing.
        quantity = floor_to_6dp(item.buy_dollars / price)
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
            # day's run re-evaluates from scratch. MUST roll back: this
            # function's caller (run_rebalance.py's _run_cycle()) shares
            # ONE db session across all 3 portfolios in a cycle.
            # record_trade_fill() had already done db.add(trade) +
            # db.flush() (assigning an id, sending the INSERT within the
            # still-open transaction) before this exception — without
            # this rollback, that orphaned insert stays pending and gets
            # silently swept into whatever THIS OR A LATER portfolio's
            # next successful db.commit() happens to do, leaving a
            # status="filled" Trade row with no actual cash/holdings/
            # ledger effect. Caught exactly this in production (Trade
            # id=107) before this fix — see the commit that added this
            # comment for the full root-cause writeup.
            db.rollback()
            continue

        trades.append(trade)

    return trades


async def sell_full_position(
    db: Session,
    portfolio: Portfolio,
    symbol: str,
    asset_type: str,
    *,
    source: str,
) -> Trade | None:
    """Sells the ENTIRE current holding of `symbol` in `portfolio`, if
    any, through the standard execute_trade()/record_trade_fill() path —
    the same path every buy in this module (and decision_loop.py,
    auto_invest_service.py) already uses, so apply_cash_delta() and the
    CashLedgerEntry audit trail happen automatically inside
    record_trade_fill(), no special-casing needed here.

    Shared by both of this module's deliberate sell exceptions (see
    module docstring): agent/run_rebalance.py's classification-flip sell
    (source="rebalance") and agent/stop_loss.py's emergency sweep
    (source="stop_loss") — `source` is the only thing that differs
    between the two call sites, everything else about "sell whatever is
    currently held, in full" is identical.

    Returns None (never raises) when there's nothing meaningful to sell:
    no holding, a zero/negative quantity, or no live price available —
    callers treat "nothing to sell" as a normal, expected outcome (e.g.
    a classification flip fired for a ticker with no existing position),
    not an error worth interrupting a cycle over.

    Rolls back and returns None on InsufficientHoldingsError rather than
    letting it propagate — defends against the exact shared-session
    orphaned-Trade-row failure mode already fixed once in this module for
    buys (see rebalance_portfolio()'s except-clause comment): both of
    this function's callers may run several sells/buys against the same
    shared db session within one cycle.
    """
    holding = next(
        (h for h in portfolio.holdings if h.symbol == symbol and h.asset_type == asset_type),
        None,
    )
    if holding is None or holding.quantity <= 0:
        return None

    price = await market_data.get_price_for_holding(symbol, asset_type)
    if price is None:
        logger.warning(
            "sell_full_position: no live price available for %s (%s), skipping sell "
            "this cycle — will retry next cycle",
            symbol, asset_type,
        )
        return None

    quantity = holding.quantity
    result = execute_trade(symbol, asset_type, "sell", quantity, simulate_only=True)
    if result.status != "filled":
        logger.warning(
            "sell_full_position: execute_trade did not fill for %s: %s", symbol, result.message
        )
        return None

    try:
        trade = record_trade_fill(
            db,
            portfolio,
            symbol,
            asset_type,
            "sell",
            quantity,
            price,
            simulated=result.simulated,
            order_id=result.order_id,
            source=source,
        )
    except InsufficientHoldingsError:
        db.rollback()
        logger.exception(
            "sell_full_position: InsufficientHoldingsError selling %s — rolled back, "
            "will retry next cycle",
            symbol,
        )
        return None

    return trade
