"""NYSE market-hours helpers shared by run_agent.py (poll-interval choice
and the precise 9:30am ET pending-trade wake), decision_loop.py (deciding
whether a stock decision executes immediately or queues), and
run_rebalance.py (its own precise 10:00am ET daily wake). Split out from
run_agent.py specifically to avoid a run_agent <-> decision_loop import
cycle (run_agent already imports from decision_loop).

Weekday + 9:30-16:00 ET, no holiday calendar (Thanksgiving, etc. reads as
"open") — same accepted MVP scope run_agent.py's is_market_hours()
originally documented; a bad poll/queue on a market holiday just finds no
fresh news, or queues for a 9:30 that turns out to be a holiday and fills
whenever the next real trading day's 9:30 wake happens to fire instead.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")
MARKET_OPEN = dtime(9, 30)
MARKET_CLOSE = dtime(16, 0)
# Fixed daily rebalance time: after MARKET_OPEN so the 9:30 queued-fill
# trigger (next_market_open(), run_agent.py) has already settled that
# day's pending trades before run_rebalance.py computes available cash.
REBALANCE_TIME = dtime(10, 0)


def is_market_hours(now_et: datetime | None = None) -> bool:
    """Weekday + 9:30-16:00 ET."""
    now_et = now_et or datetime.now(MARKET_TZ)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return MARKET_OPEN <= now_et.time() < MARKET_CLOSE


def _next_time_at(hour: int, minute: int, now_et: datetime | None = None) -> datetime:
    """Next upcoming HH:MM ET on a weekday, strictly after `now_et`
    (today's HH:MM itself if `now_et` is still before it) — shared
    weekday-skip math behind both next_market_open() and
    next_rebalance_time()."""
    now_et = now_et or datetime.now(MARKET_TZ)
    candidate = now_et.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= now_et:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def next_market_open(now_et: datetime | None = None) -> datetime:
    """The next upcoming 9:30am ET on a weekday, strictly after `now_et`
    (today's 9:30 itself if `now_et` is still before it). Used both to
    timestamp a queued off-hours trade's scheduled_execution_time and by
    run_agent.py's run_forever() to wake precisely at that instant rather
    than waiting for the next regular poll to happen to land near it."""
    return _next_time_at(MARKET_OPEN.hour, MARKET_OPEN.minute, now_et)


def next_rebalance_time(now_et: datetime | None = None) -> datetime:
    """The next upcoming REBALANCE_TIME (10:00am ET) on a weekday, same
    semantics as next_market_open() — used by run_rebalance.py's
    run_forever() to wake precisely once a day rather than polling."""
    return _next_time_at(REBALANCE_TIME.hour, REBALANCE_TIME.minute, now_et)
