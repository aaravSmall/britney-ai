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
update_streaks() to debounce every ticker's status (fixed 6 AND
discovered) and detect any CONFIRMED flip; (3) acting on those flips —
obvious -> non-obvious sells the full position (from every tier that
could hold it: just the owning tier for a fixed ticker, all 3 for a
discovered ticker in the global obvious pool — see below); (4)
agent/stop_loss.py's emergency sweep over every currently-held position
(obvious, non-obvious, and crypto); (5) the normal per-portfolio buy
loop, now reading each tier's freshly-updated obvious_pass. This is the
only place classification/streak-debounce runs — no separate standalone
process/timer for it (docs/DISCOVERY_DESIGN.md §3: a trailing-13-week/
30-day basis doesn't need to be fresher than this job's once-daily
cadence). The stop-loss sweep, by contrast, ALSO runs every 3 minutes
from agent/run_agent.py — see that module's docstring for why an
"emergency" mechanism can't wait for a once-daily cycle alone.

A discovered ticker that CONFIRMS obvious status (same 3-day debounce)
joins a GLOBAL obvious pool folded into EVERY tier's obvious bucket, not
routed to one tier like non-obvious candidates are — see
app.services.rebalance_service.effective_target_weights()'s docstring
for the redistribution math and why "available to any risk level" is the
right call for a ticker that's cleared the strict volatility gate.

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

        # 2. Debounce each ticker's status BEFORE routing/pool decisions
        # below — both route_non_obvious() and global_obvious_pool() need
        # the debounced EFFECTIVE status (not the raw daily signal), so a
        # discovered ticker that just started passing today doesn't
        # instantly vanish from non-obvious routing before it's actually
        # confirmed into the global obvious pool (see
        # classification.route_non_obvious()'s docstring point 2).
        streaks = classification.update_streaks(db, classifications)

        routed = classification.route_non_obvious(
            classifications, streaks,
            discovered_tickers=discovered_tickers,
            confidence_by_ticker=confidence_by_ticker,
        )
        global_obvious_discovered = classification.global_obvious_pool(
            streaks, discovered_tickers=discovered_tickers
        )
        logger.info(
            "Classification complete: %d ticker(s) classified, routed non-obvious "
            "counts: %s, global obvious discovered pool: %s",
            len(classifications),
            {tier: len(cands) for tier, cands in routed.items()},
            sorted(global_obvious_discovered) or "(none)",
        )

        # 3. Act on any CONFIRMED (3-consecutive-day) flip — obvious ->
        # non-obvious sells the full position (this reverses the
        # original buy-only rule, on purpose — see rebalance_service.py's
        # module docstring); non-obvious -> obvious just re-enters
        # obvious_pass/global_obvious_discovered below, no trade needed
        # for that direction (buys happen through the normal per-tier
        # loop like any other passing ticker).
        #
        # Fixed-6 tickers belong to exactly one tier, so only that tier's
        # portfolio can hold one — sell from there only. A discovered
        # ticker that was in the GLOBAL obvious pool is potentially held
        # by all 3 tiers (unlike fixed tickers, unlike volatility-routed
        # non-obvious candidates), so a confirmed flip out of it sells
        # from every tier's portfolio, not just one.
        fixed_six_by_tier = {
            tier: set(REBALANCE_TARGET_WEIGHTS[tier]["obvious"]) for tier in portfolios_by_tier
        }
        for ticker, streak in streaks.items():
            if not streak.flipped or streak.effective_status:
                continue  # only act on a CONFIRMED obvious -> non-obvious flip

            if ticker in discovered_tickers:
                sell_targets = list(portfolios_by_tier.items())  # global pool: every tier
            else:
                owning_tier = next(
                    (t for t, tickers in fixed_six_by_tier.items() if ticker in tickers), None
                )
                sell_targets = (
                    [(owning_tier, portfolios_by_tier[owning_tier])] if owning_tier else []
                )

            for tier, portfolio in sell_targets:
                logger.warning(
                    "CLASSIFICATION SELL: %s confirmed non-obvious after %d consecutive "
                    "day(s) (tier=%s, portfolio=%s) — selling full position.",
                    ticker, streak.consecutive_days, tier, portfolio.id,
                )
                # Per-item try/except, same shape as the buy loop below
                # (line ~257) — sell_full_position() only rolls back on
                # its own InsufficientHoldingsError; any OTHER exception
                # (network, DB, a bug) must not be allowed to propagate
                # out of this loop and cancel the stop-loss sweep and
                # every tier's buy loop that come after it in this same
                # cycle. One ticker's failure here just means it's
                # retried next cycle, same as a failed buy already does.
                try:
                    trade = await sell_full_position(
                        db, portfolio, ticker, "stock", source="rebalance"
                    )
                except Exception:
                    logger.exception(
                        "CLASSIFICATION SELL failed unexpectedly: portfolio=%s ticker=%s "
                        "— rolled back, will retry next cycle",
                        portfolio.id, ticker,
                    )
                    db.rollback()
                    continue
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

        # 4. Emergency stop-loss sweep — independent of the debounce
        # above, checks EVERY currently-held position (obvious,
        # non-obvious, and crypto), not just the classification-tracked
        # fixed 6. See agent/stop_loss.py's module docstring for why this
        # also runs every 3 min from agent/run_agent.py, not just here.
        # Wrapped the same way as the classification-sell loop above and
        # the buy loop below: an unexpected failure partway through the
        # sweep must not cancel every tier's buy loop that follows it in
        # this same cycle.
        try:
            await stop_loss.check_all_positions_and_sell(db)
        except Exception:
            logger.exception(
                "Stop-loss sweep failed unexpectedly — rolled back, will retry next cycle"
            )
            db.rollback()

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
            if global_obvious_discovered:
                logger.info(
                    "portfolio=%s tier=%s: global obvious discovered tickers also "
                    "targeted this cycle: %s",
                    portfolio.id, tier, ", ".join(sorted(global_obvious_discovered)),
                )
            if not obvious_pass and not global_obvious_discovered:
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
                    obvious_pass=obvious_pass,
                    global_obvious_discovered=global_obvious_discovered,
                    non_obvious_routed=non_obvious_routed,
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
                    obvious_pass=obvious_pass,
                    global_obvious_discovered=global_obvious_discovered,
                    non_obvious_routed=non_obvious_routed,
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
