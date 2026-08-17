"""Emergency stop-loss sweep — the second, independent sell mechanism
added alongside agent/classification.py's debounced classification-based
sell (Part A). See agent_config.STOP_LOSS_* for the threshold constants
and their reasoning.

Checks EVERY currently-held position across all 3 agent portfolios —
obvious-bucket fixed tickers, non-obvious/discovered holdings, AND
crypto — not just the classification-tracked fixed 6, by reading each
portfolio's live PortfolioHolding rows directly rather than iterating any
fixed ticker list. Deliberately bypasses
agent_config.CLASSIFICATION_DEBOUNCE_DAYS entirely: an emergency exit
doesn't wait 3 days to confirm anything, that's the whole point of it
being a SEPARATE mechanism from Part A rather than a shortcut inside it.

Runs from TWO places, not just the daily rebalance cycle:

  - agent/run_rebalance.py's 10am ET cycle (see that module).
  - agent/run_agent.py's 3-minute poll loop, added as a new sibling call
    in run_forever() — NOT inside agent/decision_loop.py, which this
    prompt is explicit about not touching. An "emergency" mechanism that
    only checks once a day defeats its own purpose: a position cratering
    at 11am shouldn't have to wait until tomorrow's 10am cycle to get
    sold. Checking every 3 minutes is cheap despite that: stock_data.
    history()'s existing 300s (5-minute) TTL cache means the ACTUAL
    Yahoo network call for a given ticker happens at most once per ~5
    minutes regardless of how often this function is called — a 3-min
    poll mostly re-reads the cache, it doesn't multiply real network
    load by the same factor it multiplies check frequency by.

On trigger, sells the full position immediately via
rebalance_service.sell_full_position(), tagged Trade.source="stop_loss"
(distinct from a classification-driven sell's source="rebalance").
Freed cash does NOT get reinvested this same pass — see
check_all_positions_and_sell()'s docstring for why conflating "get out
now" with "here's where to reinvest" is a bad idea in a broader selloff.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from agent import agent_config
from agent.decision_loop import RISK_TIER_BY_TOLERANCE
from app.models import Portfolio
from app.services import stock_data
from app.services.rebalance_service import sell_full_position

logger = logging.getLogger(__name__)


@dataclass
class StopLossCheck:
    portfolio_id: int
    tier: str | None
    symbol: str
    asset_type: str
    triggered: bool
    reason: str
    single_day_move: float | None
    drop_from_high: float | None


def _yahoo_symbol(symbol: str, asset_type: str) -> str:
    """Yahoo's chart endpoint (stock_data.history(), what this module
    uses for the % move checks) needs crypto tickers in "<SYMBOL>-USD"
    pair form, unlike the bare stock tickers it accepts directly. The
    actual SELL price still goes through
    app.services.market_data.get_price_for_holding() (Yahoo for stock,
    CoinGecko for crypto — see sell_full_position()), matching every
    other trade in this app; this mapping is only for the history fetch
    this module uses to compute the % moves that decide WHETHER to sell."""
    if asset_type == "crypto":
        return f"{symbol}-USD"
    return symbol


async def _check_position(
    symbol: str, asset_type: str
) -> tuple[bool, str, float | None, float | None]:
    """Returns (triggered, reason, single_day_move, drop_from_high).
    Uses the existing "30D" (Yahoo range=1mo, interval=1d) history range
    — ~21-22 trading days, comfortably covering the trailing
    STOP_LOSS_HIGH_LOOKBACK_DAYS window — rather than adding a new Yahoo
    range key, since a 10-calendar-day range isn't one of Yahoo's
    supported range strings anyway (see stock_data.RANGE_TO_YAHOO's
    comment on which range values Yahoo actually accepts)."""
    candles = await stock_data.history(_yahoo_symbol(symbol, asset_type), "30D")
    if len(candles) < 2:
        return False, f"insufficient price history ({len(candles)} candle(s))", None, None

    window = candles[-agent_config.STOP_LOSS_HIGH_LOOKBACK_DAYS :]
    trailing_high = max(c["high"] for c in window)
    latest_close = candles[-1]["close"]
    previous_close = candles[-2]["close"]

    single_day_move = (latest_close - previous_close) / previous_close if previous_close else 0.0
    drop_from_high = (latest_close - trailing_high) / trailing_high if trailing_high else 0.0

    if asset_type == "crypto":
        single_day_threshold = agent_config.STOP_LOSS_CRYPTO_SINGLE_DAY_PCT
        drop_threshold = agent_config.STOP_LOSS_CRYPTO_DROP_FROM_HIGH_PCT
    else:
        single_day_threshold = agent_config.STOP_LOSS_STOCK_SINGLE_DAY_PCT
        drop_threshold = agent_config.STOP_LOSS_STOCK_DROP_FROM_HIGH_PCT

    if single_day_move <= single_day_threshold:
        return (
            True,
            f"single-day move {single_day_move:+.2%} <= {single_day_threshold:.0%} threshold",
            single_day_move, drop_from_high,
        )
    if drop_from_high <= drop_threshold:
        return (
            True,
            f"drop from {agent_config.STOP_LOSS_HIGH_LOOKBACK_DAYS}-day high "
            f"{drop_from_high:+.2%} <= {drop_threshold:.0%} threshold",
            single_day_move, drop_from_high,
        )

    return (
        False,
        f"{single_day_move:+.2%} single-day, {drop_from_high:+.2%} from "
        f"{agent_config.STOP_LOSS_HIGH_LOOKBACK_DAYS}-day high, no trigger",
        single_day_move, drop_from_high,
    )


async def check_all_positions_and_sell(db: Session) -> list[StopLossCheck]:
    """Sweeps every currently-held position across all agent portfolios,
    selling anything that triggers. Every check is logged, even (in fact
    especially) when nothing triggers, so there's a real audit trail
    proving this ran rather than silence being ambiguous between "ran
    and found nothing" and "didn't run."

    Freed cash from a triggered sell is deliberately left as cash for
    THIS pass — no redistribution into other assets happens here, even
    though rebalance_service.effective_target_weights() has that exact
    machinery for the classification-flip sell case. A stop-loss is a
    "get out now" signal, not a "here's where to reinvest" signal;
    reinvesting the same cycle risks buying into whatever else is also
    dropping in a broader selloff that triggered this position's exit in
    the first place. The next normal rebalance cycle (10am ET) decides
    where freed cash goes, using its own fresh classification/routing —
    unchanged from how it already treats any other cash sitting in a
    portfolio.
    """
    portfolios = db.query(Portfolio).filter(Portfolio.owner_type == "agent").all()
    results: list[StopLossCheck] = []

    for portfolio in portfolios:
        tier = RISK_TIER_BY_TOLERANCE.get((portfolio.risk_tolerance or "").lower())
        for holding in list(portfolio.holdings):
            if holding.quantity <= 0:
                continue

            triggered, reason, single_day_move, drop_from_high = await _check_position(
                holding.symbol, holding.asset_type
            )
            results.append(
                StopLossCheck(
                    portfolio_id=portfolio.id, tier=tier, symbol=holding.symbol,
                    asset_type=holding.asset_type, triggered=triggered, reason=reason,
                    single_day_move=single_day_move, drop_from_high=drop_from_high,
                )
            )

            if not triggered:
                logger.info(
                    "stop-loss check: portfolio=%s tier=%s %s %s — %s",
                    portfolio.id, tier, holding.asset_type, holding.symbol, reason,
                )
                continue

            logger.warning(
                "STOP-LOSS TRIGGERED: portfolio=%s tier=%s %s %s — %s. Selling full "
                "position immediately (bypasses classification debounce).",
                portfolio.id, tier, holding.asset_type, holding.symbol, reason,
            )
            trade = await sell_full_position(
                db, portfolio, holding.symbol, holding.asset_type, source="stop_loss"
            )
            if trade:
                logger.warning(
                    "STOP-LOSS SOLD: portfolio=%s sold %s %s @ $%.2f — freed cash left "
                    "un-invested until the next normal rebalance cycle.",
                    portfolio.id, trade.quantity, trade.symbol, trade.price,
                )
            else:
                logger.warning(
                    "STOP-LOSS: %s triggered for portfolio=%s but the sell did not "
                    "execute (no live price, or already sold) — will re-check next cycle",
                    holding.symbol, portfolio.id,
                )

    return results
