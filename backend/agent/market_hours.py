"""NYSE market-hours helpers shared by run_agent.py (poll-interval choice
and the precise 9:30am ET pending-trade wake) and decision_loop.py
(deciding whether a stock decision executes immediately or queues).
Split out from run_agent.py specifically to avoid a run_agent <-> decision_loop
import cycle (run_agent already imports from decision_loop).

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


def is_market_hours(now_et: datetime | None = None) -> bool:
    """Weekday + 9:30-16:00 ET."""
    now_et = now_et or datetime.now(MARKET_TZ)
    if now_et.weekday() >= 5:  # Saturday=5, Sunday=6
        return False
    return MARKET_OPEN <= now_et.time() < MARKET_CLOSE


def next_market_open(now_et: datetime | None = None) -> datetime:
    """The next upcoming 9:30am ET on a weekday, strictly after `now_et`
    (today's 9:30 itself if `now_et` is still before it). Used both to
    timestamp a queued off-hours trade's scheduled_execution_time and by
    run_agent.py's run_forever() to wake precisely at that instant rather
    than waiting for the next regular poll to happen to land near it."""
    now_et = now_et or datetime.now(MARKET_TZ)
    candidate = now_et.replace(
        hour=MARKET_OPEN.hour, minute=MARKET_OPEN.minute, second=0, microsecond=0
    )
    if candidate <= now_et:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate
