"""Coverage for the emergency stop-loss sweep (agent/stop_loss.py) —
Part B of the sell mechanisms added alongside the debounced
classification-based sell (agent/classification.py's update_streaks(),
covered in tests/test_classification.py).

CONSTRUCTED throughout: stop_loss.py needs a sharp real crash to ever
trigger for real, which obviously isn't available on demand — every
price series here is hand-built to land on one specific side of a
threshold. Network calls (stock_data.history) are monkeypatched, same
convention as tests/test_classification.py's fake_history fixture.
"""

import asyncio

import pytest

import app.services.market_data as market_data
import app.services.stock_data as stock_data
from agent import agent_config
from agent.stop_loss import _check_position, check_all_positions_and_sell
from app.database import SessionLocal
from app.models import Portfolio, PortfolioHolding, Trade


@pytest.fixture
def fake_history(monkeypatch):
    """Maps a Yahoo-style symbol (already including "-USD" for crypto,
    since _check_position() applies that mapping before calling
    stock_data.history()) to a list of daily closes — the fixture builds
    the {open,high,low,close,volume} candle dicts stock_data.history()
    would normally return."""
    table: dict[str, list[float]] = {}

    async def _fake(symbol: str, range_key: str) -> list[dict]:
        closes = table.get(symbol, [])
        return [{"close": c, "high": c, "low": c, "open": c, "volume": 0} for c in closes]

    monkeypatch.setattr(stock_data, "history", _fake)
    return table


def _flat_then_drop(n_flat: int, flat_price: float, final_price: float) -> list[float]:
    return [flat_price] * n_flat + [final_price]


# ---------------------------------------------------------------------
# _check_position() — threshold logic, both stock and crypto bands
# ---------------------------------------------------------------------


def test_stock_single_day_drop_triggers_at_threshold(fake_history):
    # -15% single day: previous close 100 -> latest close 85.
    fake_history["XYZ"] = _flat_then_drop(9, 100.0, 85.0)
    triggered, reason, single_day, drop = asyncio.run(_check_position("XYZ", "stock"))
    assert triggered is True
    assert "single-day" in reason
    assert single_day == pytest.approx(-0.15)


def test_stock_single_day_drop_just_under_threshold_does_not_trigger(fake_history):
    # -14%: inside the -15% stock threshold, must NOT trigger.
    fake_history["XYZ"] = _flat_then_drop(9, 100.0, 86.0)
    triggered, reason, single_day, drop = asyncio.run(_check_position("XYZ", "stock"))
    assert triggered is False
    assert single_day == pytest.approx(-0.14)
    assert "no trigger" in reason


def test_stock_cumulative_drop_from_high_triggers_at_threshold(fake_history):
    # Exactly STOP_LOSS_HIGH_LOOKBACK_DAYS (10) candles, so the trailing
    # high (100, the first/oldest candle) is still inside the lookback
    # window — gradually declining (no single day >= -15%) to a final
    # close of 80 -> exactly -20% from that high.
    closes = [100.0, 98.0, 96.0, 94.0, 92.0, 90.0, 88.0, 86.0, 84.0, 80.0]
    assert len(closes) == agent_config.STOP_LOSS_HIGH_LOOKBACK_DAYS
    fake_history["XYZ"] = closes
    triggered, reason, single_day, drop = asyncio.run(_check_position("XYZ", "stock"))
    assert triggered is True
    assert "10-day high" in reason
    assert drop == pytest.approx(-0.20)
    # Confirms this is the CUMULATIVE trigger, not the single-day one —
    # no individual day here moves anywhere near -15%.
    assert single_day > -0.10


@pytest.mark.parametrize(
    "asset_type,single_day_pct,should_trigger",
    [
        ("stock", -0.15, True),
        ("stock", -0.14, False),
        ("crypto", -0.20, False),  # would trigger a stock, must NOT trigger crypto
        ("crypto", -0.30, True),
        ("crypto", -0.29, False),
    ],
)
def test_single_day_thresholds_are_asset_type_specific(fake_history, asset_type, single_day_pct, should_trigger):
    symbol = "BTC-USD" if asset_type == "crypto" else "XYZ"
    final = 100.0 * (1 + single_day_pct)
    fake_history[symbol] = _flat_then_drop(9, 100.0, final)
    ticker = "BTC" if asset_type == "crypto" else "XYZ"
    triggered, reason, single_day, drop = asyncio.run(_check_position(ticker, asset_type))
    assert triggered is should_trigger
    assert single_day == pytest.approx(single_day_pct, abs=1e-6)


def test_crypto_cumulative_drop_triggers_at_35pct_not_20pct(fake_history):
    # Exactly 10 candles (see the stock cumulative test above for why),
    # -35% from the trailing high (100, oldest candle), gradual declines
    # only (no single-day trigger).
    closes = [100.0 - i * (35.0 / 9) for i in range(10)]  # 100 -> 65.0, i.e. -35%
    assert len(closes) == agent_config.STOP_LOSS_HIGH_LOOKBACK_DAYS
    fake_history["BTC-USD"] = closes
    triggered, reason, single_day, drop = asyncio.run(_check_position("BTC", "crypto"))
    assert triggered is True
    assert drop == pytest.approx(-0.35, abs=1e-6)


# ---------------------------------------------------------------------
# check_all_positions_and_sell() — full sweep, real sell execution,
# source="stop_loss" tagging, and the classification-debounce bypass
# ---------------------------------------------------------------------


def _new_stop_loss_test_portfolio(db, *, cash_balance: float = 1_000.0) -> Portfolio:
    """owner_type="agent" is REQUIRED here — check_all_positions_and_sell()
    only queries owner_type="agent" portfolios, unlike
    tests/test_rebalance.py's owner_type="user" convention. Uses a
    risk_tolerance value that does NOT match "low"/"medium"/"high"
    deliberately, so it can never collide with
    agent.decision_loop.ensure_target_portfolios()'s (owner_type="agent",
    risk_tolerance IN {low,medium,high}) uniqueness assumption the way
    test_rebalance.py's docstring warns a real-tolerance value would."""
    portfolio = Portfolio(
        user_id=None, owner_type="agent", risk_tolerance="test-stop-loss-only",
        cash_balance=cash_balance,
    )
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


@pytest.fixture
def fixed_price(monkeypatch):
    async def _fake(symbol: str, asset_type: str) -> float:
        return 85.0

    monkeypatch.setattr(market_data, "get_price_for_holding", _fake)


def test_triggered_stop_loss_sells_full_position_tagged_source_stop_loss(
    client, fake_history, fixed_price
):
    fake_history["CRASH"] = _flat_then_drop(9, 100.0, 85.0)  # -15%, triggers

    db = SessionLocal()
    try:
        portfolio = _new_stop_loss_test_portfolio(db)
        portfolio_id = portfolio.id
        db.add(
            PortfolioHolding(
                portfolio_id=portfolio.id, symbol="CRASH", asset_type="stock",
                quantity=10.0, avg_cost=100.0,
            )
        )
        db.commit()

        results = asyncio.run(check_all_positions_and_sell(db))
    finally:
        db.close()

    crash_check = next(r for r in results if r.symbol == "CRASH")
    assert crash_check.triggered is True

    db = SessionLocal()
    try:
        holdings = {h.symbol: h for h in db.get(Portfolio, portfolio_id).holdings}
        assert "CRASH" not in holdings or holdings["CRASH"].quantity == 0

        trades = db.query(Trade).filter(Trade.portfolio_id == portfolio_id).all()
        assert len(trades) == 1
        assert trades[0].source == "stop_loss"  # NOT "rebalance"
        assert trades[0].side == "sell"
        assert trades[0].quantity == pytest.approx(10.0)
        print(f"\nStop-loss sell: {trades[0].quantity} CRASH @ ${trades[0].price} source={trades[0].source!r}")
    finally:
        db.close()


def test_no_trigger_does_not_sell(client, fake_history, fixed_price):
    fake_history["CALM"] = _flat_then_drop(9, 100.0, 99.0)  # -1%, no trigger

    db = SessionLocal()
    try:
        portfolio = _new_stop_loss_test_portfolio(db)
        portfolio_id = portfolio.id
        db.add(
            PortfolioHolding(
                portfolio_id=portfolio.id, symbol="CALM", asset_type="stock",
                quantity=5.0, avg_cost=100.0,
            )
        )
        db.commit()

        results = asyncio.run(check_all_positions_and_sell(db))
    finally:
        db.close()

    calm_check = next(r for r in results if r.symbol == "CALM")
    assert calm_check.triggered is False

    db = SessionLocal()
    try:
        holdings = {h.symbol: h for h in db.get(Portfolio, portfolio_id).holdings}
        assert holdings["CALM"].quantity == pytest.approx(5.0)  # untouched
        trades = db.query(Trade).filter(Trade.portfolio_id == portfolio_id).all()
        assert trades == []
    finally:
        db.close()


def test_stop_loss_bypasses_a_ticker_mid_streak_toward_regaining_obvious_status(
    client, fake_history, fixed_price
):
    """The explicit bypass requirement: a ticker 2 days into a 3-day
    streak toward REGAINING obvious status (i.e. classification would
    normally leave it alone for one more day either way) still gets
    emergency-sold immediately on a crash — stop_loss.py never consults
    TickerStreakState at all, structurally cannot be blocked by it."""
    from datetime import date

    from agent.classification import update_streaks, ClassificationResult

    fake_history["MIDSTREAK"] = _flat_then_drop(9, 100.0, 85.0)  # -15%, triggers

    db = SessionLocal()
    try:
        # Build a real "2 days into regaining obvious status" streak
        # state — currently non-obvious (effective_status=False), 2
        # consecutive passing raw days so far, one day short of flipping.
        def _classification(is_obvious: bool) -> ClassificationResult:
            return ClassificationResult(
                ticker="MIDSTREAK", is_obvious=is_obvious,
                gate_a_passed=is_obvious, gate_a_detail="",
                gate_b_passed=is_obvious, gate_b_detail="",
                gate_c_passed=is_obvious, gate_c_detail="",
                volatility=None, voo_volatility=None, vol_ratio=None,
            )

        update_streaks(db, {"MIDSTREAK": _classification(False)}, today=date(2026, 1, 1))  # bootstrap non-obvious
        update_streaks(db, {"MIDSTREAK": _classification(True)}, today=date(2026, 1, 2))  # pass day 1
        mid = update_streaks(db, {"MIDSTREAK": _classification(True)}, today=date(2026, 1, 3))["MIDSTREAK"]  # pass day 2
        assert mid.consecutive_days == 2
        assert mid.effective_status is False  # NOT yet obvious — still mid-streak

        portfolio = _new_stop_loss_test_portfolio(db)
        portfolio_id = portfolio.id
        db.add(
            PortfolioHolding(
                portfolio_id=portfolio.id, symbol="MIDSTREAK", asset_type="stock",
                quantity=7.0, avg_cost=100.0,
            )
        )
        db.commit()

        results = asyncio.run(check_all_positions_and_sell(db))
    finally:
        db.close()

    check = next(r for r in results if r.symbol == "MIDSTREAK")
    assert check.triggered is True  # crash still fires regardless of streak state

    db = SessionLocal()
    try:
        holdings = {h.symbol: h for h in db.get(Portfolio, portfolio_id).holdings}
        assert "MIDSTREAK" not in holdings or holdings["MIDSTREAK"].quantity == 0
        trades = db.query(Trade).filter(Trade.portfolio_id == portfolio_id).all()
        assert len(trades) == 1
        assert trades[0].source == "stop_loss"
        print(
            f"\nBypass confirmed: MIDSTREAK was 2/3 days into regaining obvious status "
            f"(effective_status still False) yet stop-loss sold it anyway on the crash."
        )
    finally:
        db.close()
