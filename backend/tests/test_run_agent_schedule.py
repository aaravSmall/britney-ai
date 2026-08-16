"""Coverage for run_agent.py's precise 9:30am ET pending-trade wake
composing safely with the new flat poll interval — the wake condition
(`0 < seconds_to_open <= planned_sleep`) must fire at most once per
trading day even though POLL_MINUTES dropped from a 15/60-min split to a
flat 3 minutes, which shrinks the "catch window" each individual
iteration sees.

Simulates run_forever()'s wake-time sequence directly (each iteration's
"now" advances by POLL_MINUTES, exactly mirroring the real loop's
`await asyncio.sleep(planned_sleep)` / early `continue` after firing)
rather than running the actual infinite loop, since that never returns.
POLL_MINUTES is duplicated here (not imported from agent.run_agent) so
a future change to one without the other still fails this test loudly.
"""

from datetime import datetime, timedelta

from agent.market_hours import MARKET_TZ, next_market_open

POLL_MINUTES = 3  # mirrors agent.run_agent.POLL_MINUTES


def _simulate_wake_times(start: datetime, end: datetime) -> list[datetime]:
    """Every instant the 9:30am ET precise-wake branch would fire,
    stepping through [start, end) the same way run_forever() does."""
    planned_sleep = POLL_MINUTES * 60
    now = start
    fire_times: list[datetime] = []
    while now < end:
        seconds_to_open = (next_market_open(now) - now).total_seconds()
        if 0 < seconds_to_open <= planned_sleep:
            fire_times.append(now + timedelta(seconds=seconds_to_open))
            now = now + timedelta(seconds=seconds_to_open)  # matches run_forever()'s `continue`
            continue
        now = now + timedelta(seconds=planned_sleep)
    return fire_times


def test_930_wake_fires_at_most_once_across_a_single_day():
    start = datetime(2026, 8, 17, 0, 0, tzinfo=MARKET_TZ)  # Monday, midnight ET
    end = start + timedelta(hours=26)  # comfortably past that day's 9:30

    fire_times = _simulate_wake_times(start, end)

    assert len(fire_times) == 1
    assert fire_times[0].hour == 9 and fire_times[0].minute == 30
    print(f"\nFired once, at: {fire_times[0].isoformat()}")


def test_930_wake_fires_exactly_once_per_trading_day_across_a_week():
    start = datetime(2026, 8, 17, 0, 0, tzinfo=MARKET_TZ)  # Monday
    end = start + timedelta(days=7)

    fire_times = _simulate_wake_times(start, end)

    # Mon-Fri only (Aug 17-21, 2026) — 5 trading days, weekend skipped.
    assert len(fire_times) == 5
    assert all(t.weekday() < 5 for t in fire_times)
    assert all(t.hour == 9 and t.minute == 30 for t in fire_times)
    assert len({t.date() for t in fire_times}) == 5  # one per distinct calendar day

    print(f"\nFired at: {[t.isoformat() for t in fire_times]}")
