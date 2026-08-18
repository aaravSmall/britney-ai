"""Shared quantity-sizing math for every buy/sell computed from a dollar
amount or a fraction of a holding — one place so the floor-not-round fix
from the 2026-08-17/18 orphaned-Trade production incident isn't
duplicated (and doesn't drift) across every call site that needs it.
"""

import math

_PRECISION = 1_000_000  # 6 decimal places


def floor_to_6dp(value: float) -> float:
    """Floors `value` to 6 decimal places — deliberately NOT round() or
    ceil(). Flooring guarantees floor_to_6dp(dollar_amount / price) *
    price <= dollar_amount always, so a buy sized this way can never trip
    InsufficientFundsError on a fully-funded order from a fraction-of-a-
    cent rounding-up error. That's the exact real production mechanism
    this fixes: the old `round(buy_dollars / price, 6)` produced
    quantity=1.138022, and 1.138022 * price=$226.383 = $257.628834 — a
    hair above a $257.6287746 cash balance, tripping
    portfolio_service._settle_fill()'s InsufficientFundsError on what
    should have been a fully-funded buy (and, before rebalance_service.py
    added a rollback, orphaning the already-flushed status="filled" Trade
    row that caused). The same floor keeps a holding-percentage sell
    quantity from rounding up past what it started from, for the same
    reason on the sell side.
    """
    return math.floor(value * _PRECISION) / _PRECISION
