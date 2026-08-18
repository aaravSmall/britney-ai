"""Property-based coverage for app/trading/sizing.py's floor_to_6dp() —
the single shared helper now used by every buy-sizing call site that used
to duplicate the round()-vs-floor() bug fixed in the 2026-08-17/18
production incident (rebalance_service.py's rebalance_portfolio(),
auto_invest_service.py's execute_schedule(), and decision_loop.py's
_execute()/_queue()). One property test against the shared function
covers all of them, rather than 4 near-duplicate fuzz tests re-testing
the same math at each call site.

The two hardcoded-numbers regression tests from the original incident —
test_rebalance.py's test_exact_cash_clamp_rounding_does_not_raise_
insufficient_funds and test_insufficient_funds_error_rolls_back_and_
leaves_no_orphaned_trade — stay exactly as they are; they document the
real incident precisely and this file doesn't replace them. This file
also pins those same real numbers as a Hypothesis @example, so they're
covered here too, guaranteed, alongside the random exploration.
"""

from hypothesis import example, given
from hypothesis import strategies as st

from app.trading.sizing import floor_to_6dp

prices = st.floats(min_value=0.01, max_value=10_000, allow_nan=False, allow_infinity=False)
dollar_amounts = st.floats(min_value=0.01, max_value=100_000, allow_nan=False, allow_infinity=False)


@given(price=prices, dollar_amount=dollar_amounts)
@example(price=226.383, dollar_amount=257.6287746000014)  # the real incident's numbers
def test_floor_to_6dp_never_lets_quantity_times_price_exceed_dollar_amount(price, dollar_amount):
    """The exact property the incident fix depends on: flooring
    dollar_amount/price to 6 decimals must never let quantity*price
    exceed dollar_amount, for ANY price/dollar_amount pair — not just the
    one historical case. This is what round() violated."""
    quantity = floor_to_6dp(dollar_amount / price)
    assert quantity * price <= dollar_amount
    assert quantity >= 0


@given(price=prices, dollar_amount=dollar_amounts)
def test_floor_to_6dp_is_never_more_than_a_millionth_below_the_true_quotient(price, dollar_amount):
    """Flooring must not throw away more precision than the 6-decimal
    step size — guards against a degenerate "always return 0" helper
    trivially satisfying the cost<=dollar_amount property above."""
    true_quotient = dollar_amount / price
    quantity = floor_to_6dp(dollar_amount / price)
    assert true_quotient - quantity < 1e-6 + 1e-9  # 1e-9 slack for float error
