"""
Scheduler entrypoint for the trading agent's target-allocation rebalance
job — a separate, independent process from both agent/run_agent.py
(news-driven buy/sell) and agent/run_auto_invest.py (recurring user
$-amount buys). Shaped like run_auto_invest.py (own asyncio.run()
entrypoint, own SessionLocal, bootstrap_schema() on startup, never lets
one portfolio's failure block the rest) but wakes once per trading day at
a fixed 10:00am ET (agent/market_hours.py's REBALANCE_TIME/
next_rebalance_time()) rather than polling a fixed interval — chosen to
run after next_market_open() (9:30am ET)'s queued off-hours trades from
the previous session have already settled (run_agent.py's precise 9:30
wake), so this job's "available cash" isn't computed mid-fill.

Only rebalances the agent's three risk-tier model portfolios — same
scope as run_agent.py/decision_loop.py, not real users' portfolios. See
app.services.rebalance_service.REBALANCE_TARGET_WEIGHTS for each tier's
target allocation, and that module's docstring for why crypto is never
part of this job's target math.

Mostly still buy-only (an overweight-but-passing position is never sold
down), but with two deliberate exceptions now — see
app.services.rebalance_service's module docstring for the full reasoning:
a fixed-6 ticker's CONFIRMED (3-consecutive-day debounced) flip from
obvious to non-obvious sells its full position and redistributes its
weight to the tier's remaining passing obvious tickers (this cycle, right
after classification — see _run_cycle() below); and the independent
emergency stop-loss (agent/stop_loss.py), which this cycle also runs,
bypassing the debounce entirely on a sharp drop.

Each cycle now runs, in order: (1) agent/classification.py's obvious/
non-obvious classification, ONCE (not once per tier/portfolio); (2)
update_streaks() to debounce each fixed ticker's status and detect any
CONFIRMED flip, executing a sell for each one found; (3)
agent/stop_loss.py's emergency sweep over every currently-held position
(obvious, non-obvious, and crypto); (4) the normal per-portfolio buy
loop, now reading each tier's freshly-updated obvious_pass. This is the
only place classification/streak-debounce runs — no separate standalone
process/timer for it (docs/DISCOVERY_DESIGN.md §3: a trailing-13-week/
30-day basis doesn't need to be fresher than this job's once-daily
cadence). The stop-loss sweep, by contrast, ALSO runs every 3 minutes
from agent/run_agent.py — see that module's docstring for why an
"emergency" mechanism can't wait for a once-daily cycle alone.

On the droplet, backend/deploy/britney-rebalance.timer is what actually
fires this daily (systemd's own calendar scheduling, `--once` each time)
rather than the run_forever() loop below — a timer survives a droplet
reboot/deploy restart more simply than a process that has to stay
resident 24/7 just to wake once a day. run_forever() (no args) still
works standalone for local dev/testing without systemd.

    cd backend && python -m agent.run_rebalance             # run forever, daily 10am ET
    cd backend && python -m agent.run_rebalance --once             # one cycle now, then exit
    cd backend && python -m agent.run_rebalance --once --dry-run   # compute + log the plan, buy nothing
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime

from agent import classification, stop_loss
from agent.decision_loop import RISK_TIER_BY_TOLERANCE, ensure_target_portfolios
from agent.market_hours import MARKET_TZ, next_rebalance_time
from app.database import SessionLocal, bootstrap_schema
from app.models import Portfolio
from app.services import discovery_service
from app.services.rebalance_service import (
    REBALANCE_TARGET_WEIGHTS,
    plan_rebalance,
    rebalance_portfolio,
    sell_full_position,
)

logger = logging.getLogger("agent.run_rebalance")


def _configure_logging() -> None:
    """Stdout logging only — same reasoning as run_auto_invest.py's
    _configure_logging: under systemd that's captured by journald for
    free, and this script doesn't need AGENT_LOG_FILE-style rotation."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def _tier_for(portfolio: Portfolio) -> str | None:
    return RISK_TIER_BY_TOLERANCE.get((portfolio.risk_tolerance or "").lower())


async def _run_cycle(*, dry_run: bool = False) -> None:
    db = SessionLocal()
    try:
        portfolio_ids = list(ensure_target_portfolios(db).values())
        portfolios_by_tier: dict[str, Portfolio] = {}
        for portfolio_id in portfolio_ids:
            portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).one_or_none()
            if portfolio is None:
                continue
            tier = _tier_for(portfolio)
            if tier is None:
                logger.warning(
                    "portfolio=%s risk_tolerance=%r has no recognized tier, skipping",
                    portfolio.id, portfolio.risk_tolerance,
                )
                continue
            portfolios_by_tier[tier] = portfolio

        # 1. Classification runs ONCE per cycle, shared across all 3
        # tiers/portfolios below — not once per portfolio. See
        # agent/classification.py's module docstring for why it belongs
        # here rather than a separate standalone process.
        logger.info("Running obvious/non-obvious classification for this cycle...")
        classifications = await classification.classify_all(db)
        discovered_tickers = discovery_service.distinct_discovered_tickers(db)
        confidence_by_ticker = discovery_service.latest_confidence_by_ticker(db)
        routed = classification.route_non_obvious(
            classifications,
            discovered_tickers=discovered_tickers,
            confidence_by_ticker=confidence_by_ticker,
        )
        logger.info(
            "Classification complete: %d ticker(s) classified, routed non-obvious "
            "counts: %s",
            len(classifications),
            {tier: len(cands) for tier, cands in routed.items()},
        )

        # 2. Debounce each ticker's status, and act on any CONFIRMED
        # (3-consecutive-day) flip — obvious->non-obvious sells the full
        # position (this reverses the original buy-only rule, on
        # purpose — see rebalance_service.py's module docstring);
        # non-obvious->obvious just re-enters obvious_pass below, no
        # trade needed for that direction (buys happen through the
        # normal per-tier loop like any other passing ticker).
        streaks = classification.update_streaks(db, classifications)
        for tier, portfolio in portfolios_by_tier.items():
            for ticker in REBALANCE_TARGET_WEIGHTS[tier]["obvious"]:
                streak = streaks.get(ticker)
                if streak is None or not streak.flipped or streak.effective_status:
                    continue  # only act on a CONFIRMED obvious -> non-obvious flip
                logger.warning(
                    "CLASSIFICATION SELL: %s confirmed non-obvious after %d consecutive "
                    "day(s) (tier=%s, portfolio=%s) — selling full position.",
                    ticker, streak.consecutive_days, tier, portfolio.id,
                )
                trade = await sell_full_position(db, portfolio, ticker, "stock", source="rebalance")
                if trade:
                    logger.warning(
                        "CLASSIFICATION SELL executed: portfolio=%s sold %s %s @ $%.2f",
                        portfolio.id, trade.quantity, trade.symbol, trade.price,
                    )
                else:
                    logger.info(
                        "%s flipped obvious -> non-obvious (tier=%s) but there was no "
                        "existing position to sell",
                        ticker, tier,
                    )

        # 3. Emergency stop-loss sweep — independent of the debounce
        # above, checks EVERY currently-held position (obvious,
        # non-obvious, and crypto), not just the classification-tracked
        # fixed 6. See agent/stop_loss.py's module docstring for why this
        # also runs every 3 min from agent/run_agent.py, not just here.
        await stop_loss.check_all_positions_and_sell(db)

        for tier, portfolio in portfolios_by_tier.items():
            obvious_base = REBALANCE_TARGET_WEIGHTS[tier]["obvious"]
            obvious_pass = {
                ticker
                for ticker in obvious_base
                if streaks.get(ticker) is not None and streaks[ticker].effective_status
            }
            failed_obvious = set(obvious_base) - obvious_pass
            if failed_obvious:
                logger.info(
                    "portfolio=%s tier=%s: %s currently non-obvious (debounced) — no "
                    "new buys for it this cycle",
                    portfolio.id, tier, ", ".join(sorted(failed_obvious)),
                )
            if not obvious_pass:
                total_obvious_pct = sum(obvious_base.values()) * 100
                logger.warning(
                    "portfolio=%s tier=%s: ZERO obvious candidates pass this cycle — "
                    "%.1f%% of invested target is unbuyable and will sit as cash "
                    "this cycle.",
                    portfolio.id, tier, total_obvious_pct,
                )
            non_obvious_routed = [(c.ticker, c.confidence) for c in routed.get(tier, [])]

            if dry_run:
                plan = await plan_rebalance(
                    db, portfolio, tier,
                    obvious_pass=obvious_pass, non_obvious_routed=non_obvious_routed,
                )
                for item in plan:
                    logger.info(
                        "[DRY RUN] portfolio=%s tier=%s symbol=%s current=%.1f%% "
                        "target=%.1f%% shortfall=%.1f%% buy=$%.2f%s",
                        portfolio.id, tier, item.symbol,
                        item.current_weight * 100, item.target_weight * 100,
                        item.shortfall_weight * 100, item.buy_dollars,
                        f" — SKIP: {item.skip_reason}" if item.skip_reason else " — WOULD BUY",
                    )
                continue

            try:
                trades = await rebalance_portfolio(
                    db, portfolio, tier,
                    obvious_pass=obvious_pass, non_obvious_routed=non_obvious_routed,
                )
                if trades:
                    logger.info(
                        "portfolio=%s tier=%s rebalanced: %s",
                        portfolio.id, tier,
                        "; ".join(
                            f"{t.side} {t.quantity} {t.symbol} @ ${t.price:,.2f}" for t in trades
                        ),
                    )
                else:
                    logger.info(
                        "portfolio=%s tier=%s: nothing to buy (within tolerance or "
                        "cash unavailable)",
                        portfolio.id, tier,
                    )
            except Exception:
                logger.exception(
                    "portfolio=%s tier=%s rebalance failed, will retry next cycle", portfolio.id, tier
                )
                db.rollback()
    finally:
        db.close()


async def run_forever() -> None:
    while True:
        seconds = (next_rebalance_time() - datetime.now(MARKET_TZ)).total_seconds()
        logger.info("Sleeping %d sec until next 10:00am ET rebalance run", int(seconds))
        await asyncio.sleep(seconds)
        await _run_cycle()


def _main() -> None:
    _configure_logging()
    logger.info("Ensuring database schema exists (create_all + column sync)")
    bootstrap_schema()

    once = "--once" in sys.argv[1:]
    dry_run = "--dry-run" in sys.argv[1:]

    if once:
        asyncio.run(_run_cycle(dry_run=dry_run))
    else:
        asyncio.run(run_forever())


if __name__ == "__main__":
    _main()
