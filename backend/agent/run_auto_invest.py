"""
Scheduler entrypoint for recurring user auto-invest schedules.

Shaped like agent/run_agent.py (own asyncio.run() entrypoint, own
SessionLocal, bootstrap_schema() on startup, while-True + sleep) but
polls AutoInvestSchedule rows instead of agent portfolios, and has no
market-hours awareness — a schedule fires whenever its interval has
elapsed, regardless of whether the market happens to be open (matches
existing paper-trading behavior elsewhere: nothing in this app gates
simulated fills on market hours).

Each cycle: load every enabled schedule, execute the ones is_due()
says are due (via app/services/auto_invest_service.execute_schedule,
which reuses the same execute_trade/record_trade_fill path
routes/trading.py and agent/decision_loop.py already use), log
loudly, and never let one schedule's failure (e.g. insufficient
funds) block the rest.

Known limitation (accepted for local-dev MVP): if this process isn't
running when a schedule comes due, that interval is silently skipped,
not backfilled, once the process restarts — there's no catch-up logic.

    cd backend && python -m agent.run_auto_invest
"""

from __future__ import annotations

import asyncio
import logging

from app.database import SessionLocal, bootstrap_schema
from app.models import AutoInvestSchedule
from app.services.auto_invest_service import execute_schedule, is_due
from app.services.portfolio_service import InsufficientFundsError, InsufficientHoldingsError

logger = logging.getLogger("agent.run_auto_invest")

POLL_SECONDS = 5 * 60


def _configure_logging() -> None:
    """Stdout logging only — under systemd that's captured by journald
    for free, no setup needed. Simpler than run_agent.py's
    _configure_logging (no AGENT_LOG_FILE rotation option): that's
    speculative infra this script doesn't need yet, and duplicating the
    ~5 lines this actually needs beats importing a private helper out of
    a sibling script."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )


async def _run_cycle() -> None:
    db = SessionLocal()
    try:
        schedules = db.query(AutoInvestSchedule).filter(AutoInvestSchedule.enabled).all()
        for schedule in schedules:
            if not is_due(schedule):
                logger.info(
                    "schedule=%s ticker=%s interval=%s not due (last_executed_at=%s)",
                    schedule.id,
                    schedule.ticker,
                    schedule.interval,
                    schedule.last_executed_at,
                )
                continue
            try:
                trade = await execute_schedule(db, schedule)
                logger.info(
                    "schedule=%s ticker=%s executed: %s %s @ %s (trade=%s)",
                    schedule.id,
                    schedule.ticker,
                    trade.side,
                    trade.quantity,
                    trade.price,
                    trade.id,
                )
            except (InsufficientFundsError, InsufficientHoldingsError, ValueError):
                # Expected, per-schedule failure modes (can't afford it,
                # no price available) — log loudly and move on to the
                # next schedule; last_executed_at is untouched, so it
                # stays due and will retry next cycle.
                logger.exception(
                    "schedule=%s ticker=%s execution failed, will retry next cycle",
                    schedule.id,
                    schedule.ticker,
                )
                db.rollback()
            except Exception:
                logger.exception(
                    "schedule=%s ticker=%s unexpected failure, will retry next cycle",
                    schedule.id,
                    schedule.ticker,
                )
                db.rollback()
    finally:
        db.close()


async def run_forever() -> None:
    while True:
        await _run_cycle()
        logger.info("Sleeping %d min", POLL_SECONDS // 60)
        await asyncio.sleep(POLL_SECONDS)


def _main() -> None:
    _configure_logging()
    logger.info("Ensuring database schema exists (create_all + column sync)")
    bootstrap_schema()
    asyncio.run(run_forever())


if __name__ == "__main__":
    _main()
