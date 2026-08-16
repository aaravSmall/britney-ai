"""
Scheduler entrypoint for the britney.ai trading agent.

Loops agent.decision_loop.run_once() over the agent's target portfolios
(or explicit portfolio ids), polling more often during US stock market
hours and less often outside them. After each portfolio's decision run —
whether or not it traded — also calls agent.snapshot.snapshot_capture()
for that portfolio, so PortfolioSnapshot history tracks the same cadence.

Market-hours check is intentionally simple — weekday + 9:30-16:00 ET, no
holiday calendar yet — and stock-market-only, matching decision_loop's
current stock-only scope (crypto is out of scope until the plan in
docs/IDEAS.txt's "AGENT — CRYPTO NEWS" section is built; crypto trades
24/7 so it would need its own cadence, not this one).

    cd backend && python -m agent.run_agent            # default: the 3 agent portfolios
    cd backend && python -m agent.run_agent 1 2 3       # explicit portfolio ids
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler

from agent.decision_loop import ensure_target_portfolios, fill_due_pending_trades, run_once
from agent.market_hours import MARKET_TZ, is_market_hours, next_market_open
from agent.snapshot import snapshot_capture
from app.database import SessionLocal, bootstrap_schema

logger = logging.getLogger("agent.run_agent")


def _configure_logging() -> None:
    """Always logs to stdout/stderr — under systemd that's captured by
    journald for free (`journalctl -u britney-agent`), no setup needed.
    If AGENT_LOG_FILE is set (see backend/deploy/agent.env.example), also
    writes to that file with rotation, for deployments that want a plain
    log file too. Unset by default, including in local dev."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = os.environ.get("AGENT_LOG_FILE")
    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir:
            os.makedirs(log_dir, exist_ok=True)
        handlers.append(RotatingFileHandler(log_file, maxBytes=10_000_000, backupCount=5))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


_configure_logging()

MARKET_HOURS_POLL_MINUTES = 15
AFTER_HOURS_POLL_MINUTES = 60


async def _run_all(portfolio_ids: list[int]) -> None:
    for portfolio_id in portfolio_ids:
        try:
            summary = await run_once(portfolio_id)
            logger.info(
                "portfolio=%s tier=%s articles=%s decisions=%s trades=%s",
                summary.portfolio_id,
                summary.risk_tier,
                summary.articles_considered,
                summary.decisions_made,
                summary.trades_executed,
            )
        except Exception:
            logger.exception("Agent run failed for portfolio %s", portfolio_id)

        # Snapshot regardless of whether the decision run above succeeded,
        # traded, or failed — a snapshot failure must never block or crash
        # the trading logic for this or any other portfolio.
        # snapshot_capture() already logs its own success/skip/failure
        # loudly and never raises for expected failure modes; this
        # try/except is just a last-resort backstop against a truly
        # unexpected crash taking down the rest of the loop.
        try:
            await snapshot_capture(portfolio_id)
        except Exception:
            logger.exception("Snapshot capture crashed unexpectedly for portfolio %s", portfolio_id)


async def _fill_pending_trades() -> None:
    db = SessionLocal()
    try:
        filled = await fill_due_pending_trades(db)
        if filled:
            logger.info("Filled %d pending trade(s) queued for market open", filled)
    except Exception:
        logger.exception("Filling due pending trades crashed unexpectedly")
    finally:
        db.close()


async def run_forever(portfolio_ids: list[int] | None = None) -> None:
    while True:
        ids = portfolio_ids
        if ids is None:
            db = SessionLocal()
            try:
                ids = list(ensure_target_portfolios(db).values())
            finally:
                db.close()

        # Catch-up safety net: also attempted every regular cycle (not just
        # the precise 9:30 wake below), so a pending trade from a night the
        # process happened to be down/restarting still fills on the first
        # cycle after startup instead of waiting for the next 9:30 open.
        # fill_due_pending_trades() only touches rows whose
        # scheduled_execution_time has actually arrived, so this is a
        # no-op on every cycle where nothing is due.
        await _fill_pending_trades()

        await _run_all(ids)

        market_open = is_market_hours()
        poll_minutes = MARKET_HOURS_POLL_MINUTES if market_open else AFTER_HOURS_POLL_MINUTES
        planned_sleep = poll_minutes * 60

        # Precise 9:30am ET wake for queued off-hours trades, independent of
        # the regular 15/60-min poll cadence above (which is not reliably
        # going to land exactly on market open). If the next open falls
        # inside the sleep we were about to take, cut that sleep short,
        # fill due pending trades right at open, then let the loop's next
        # iteration (already market-hours by then) resume normal polling —
        # this never changes the poll_minutes computed above, it only adds
        # one extra precisely-timed wake.
        seconds_to_open = (next_market_open() - datetime.now(MARKET_TZ)).total_seconds()
        if 0 < seconds_to_open <= planned_sleep:
            logger.info("Sleeping %d sec until 9:30am ET market open", int(seconds_to_open))
            await asyncio.sleep(seconds_to_open)
            await _fill_pending_trades()
            continue

        logger.info(
            "Sleeping %d min (%s)",
            poll_minutes,
            "market hours" if market_open else "after-hours/weekend",
        )
        await asyncio.sleep(planned_sleep)


def _main() -> None:
    # On a fresh droplet nothing has ever started the FastAPI app (which
    # normally does this in app/main.py's lifespan), so run_agent.py must
    # be able to bootstrap its own schema rather than assume it exists.
    # See app.database.bootstrap_schema's docstring for why this is more
    # than a bare create_all().
    logger.info("Ensuring database schema exists (create_all + column sync)")
    bootstrap_schema()
    portfolio_ids = [int(a) for a in sys.argv[1:]] or None
    asyncio.run(run_forever(portfolio_ids))


if __name__ == "__main__":
    _main()
