"""
Scheduler entrypoint for the britney.ai trading agent.

Loops agent.decision_loop.run_once() over the agent's target portfolios
(or explicit portfolio ids), on a flat interval, 24/7 — see POLL_MINUTES
below for why 3 minutes and why market hours no longer change it. After
each portfolio's decision run — whether or not it traded — also calls
agent.snapshot.snapshot_capture() for that portfolio, so PortfolioSnapshot
history tracks the same cadence.

is_market_hours() (agent/market_hours.py) still gates whether a stock
decision executes immediately or queues (agent/decision_loop.py) — that's
unchanged. It's no longer used here to choose the poll interval, only for
this file's own log line.

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

# Flat 24/7 interval, replacing the old 15-min-market-hours/60-min-
# after-hours split. Picked 3 minutes rather than the extremes of the
# 2-5 min range the investigation into this called for:
#   - Rate limit isn't the constraint: a full sweep is 6 Finnhub calls
#     across 3 portfolios (2 stock tickers each), so even at 3-min cadence
#     that's ~2 calls/min average — over an order of magnitude under
#     Finnhub's free-tier ~60/min ceiling (see news_ingestion.py's
#     DEFAULT_CALLS_PER_MINUTE comment). CryptoPanic calls stay at zero
#     real network cost regardless of cadence: with no CRYPTOPANIC_API_KEY
#     configured, fetch_crypto_news() returns mock articles synchronously,
#     no HTTP call at all (see news_ingestion.py's fetch_crypto_news()).
#   - 1 minute or faster buys nothing: market_data.py's price cache
#     (TTL_SECONDS = 60) means polling faster than that just re-serves the
#     same cached Yahoo quote instead of a fresher one.
#   - Yahoo's unofficial stock-quote endpoint (market_data.py) is
#     undocumented/unofficial, unlike Finnhub's official free tier — 3 min
#     is meaningfully faster (5-20x) than the old 15/60 split without
#     hammering an endpoint that could rate-limit or block without notice.
POLL_MINUTES = 3


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

        planned_sleep = POLL_MINUTES * 60

        # Precise 9:30am ET wake for queued off-hours trades, independent of
        # the flat poll cadence above (which isn't reliably going to land
        # exactly on market open just by chance). If the next open falls
        # inside the sleep we were about to take, cut that sleep short, fill
        # due pending trades right at open, then let the loop's next
        # iteration resume normal polling — this never changes
        # planned_sleep, it only adds one extra precisely-timed wake.
        # Self-limiting to at most once per day regardless of how short
        # planned_sleep is: next_market_open() always returns a time
        # strictly after "now", so the instant this branch fires and that
        # instant becomes "now", the next call to next_market_open() jumps
        # to the *next* trading day — there is no way for this condition to
        # re-evaluate True again until then. Covered by
        # tests/test_run_agent_schedule.py.
        seconds_to_open = (next_market_open() - datetime.now(MARKET_TZ)).total_seconds()
        if 0 < seconds_to_open <= planned_sleep:
            logger.info("Sleeping %d sec until 9:30am ET market open", int(seconds_to_open))
            await asyncio.sleep(seconds_to_open)
            await _fill_pending_trades()
            continue

        logger.info(
            "Sleeping %d min (%s)",
            POLL_MINUTES,
            "market hours" if is_market_hours() else "after-hours/weekend",
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
