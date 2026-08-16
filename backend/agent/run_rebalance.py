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
part of this job's target math. Buy-only: this never sells an overweight
position down, so a tier that's already over target on one asset (e.g.
from news-driven trading) just sits there — selling to rebalance is a
separate future change.

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

from agent.decision_loop import RISK_TIER_BY_TOLERANCE, ensure_target_portfolios
from agent.market_hours import MARKET_TZ, next_rebalance_time
from app.database import SessionLocal, bootstrap_schema
from app.models import Portfolio
from app.services.rebalance_service import plan_rebalance, rebalance_portfolio

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

            if dry_run:
                plan = await plan_rebalance(db, portfolio, tier)
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
                trades = await rebalance_portfolio(db, portfolio, tier)
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
