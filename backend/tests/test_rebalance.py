"""Coverage for the target-allocation rebalance job
(app/services/rebalance_service.py, agent/run_rebalance.py).

Uses freshly-created, isolated Portfolio rows per test (via SessionLocal
directly) rather than the app's 3 shared singleton agent portfolios
(news-driven trading/other test files never mutate those, and this file
shouldn't be the first to start doing so — same isolation reasoning
test_cash_ledger.py's module docstring gives for using distinct tokens
per test). rebalance_service's functions take a Portfolio + tier string
directly and never read Portfolio.owner_type, so these rows deliberately
use owner_type="user" rather than "agent" — an owner_type="agent" row
here would satisfy agent.decision_loop.ensure_target_portfolios()'s
`(owner_type="agent", risk_tolerance=X)` uniqueness assumption right
alongside the 3 real ones, and multiple tests in this file creating one
each (all defaulting to risk_tolerance="low") broke that assumption:
GET /portfolios (in a *different* test file, running later in the same
shared test DB) started raising MultipleResultsFound the moment more
than one such row existed. Caught via a real cross-file failure, not
proactively — worth remembering if this pattern is reused elsewhere.

Stock price lookups go through a monkeypatched
market_data.get_price_for_holding, same convention
test_pending_trades.py established (avoids a live Yahoo network call for
a fixed, predictable price) — rebalance_service.build_portfolio_summary's
holdings valuation and its own price-for-buy lookups both resolve
through the same module-level function, so one monkeypatch covers both.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

import app.services.market_data as market_data
from app.database import SessionLocal
from app.models import CashLedgerEntry, Portfolio, PortfolioHolding
from app.services.portfolio_service import queue_pending_trade
from app.services.rebalance_service import (
    REBALANCE_TARGET_WEIGHTS,
    plan_rebalance,
    rebalance_portfolio,
)

FAKE_PRICES = {"VOO": 500.0, "BND": 80.0}


@pytest.fixture
def fixed_prices(monkeypatch):
    async def _fake(symbol: str, asset_type: str) -> float:
        return FAKE_PRICES.get(symbol, 100.0)

    monkeypatch.setattr(market_data, "get_price_for_holding", _fake)
    return FAKE_PRICES


def _new_agent_portfolio(db, *, cash_balance: float, risk_tolerance: str = "low") -> Portfolio:
    """owner_type="user" (not "agent") is deliberate — see module
    docstring: rebalance_service never reads owner_type, but
    ensure_target_portfolios() elsewhere does, and would collide with
    these throwaway rows if they claimed to be real agent portfolios."""
    portfolio = Portfolio(
        user_id=None, owner_type="user", risk_tolerance=risk_tolerance, cash_balance=cash_balance
    )
    db.add(portfolio)
    db.commit()
    db.refresh(portfolio)
    return portfolio


def test_conservative_target_weights_have_no_crypto_and_sum_with_5pct_cash():
    """Locks in the actual numbers from the task: VOO 60% / BND 35%,
    5% implied cash, no crypto entry at all."""
    targets = REBALANCE_TARGET_WEIGHTS["conservative"]
    assert targets == {"VOO": 0.60, "BND": 0.35}
    assert sum(targets.values()) == pytest.approx(0.95)  # remaining 5% is cash
    assert "BTC" not in targets


def test_plan_rebalance_all_cash_buys_full_shortfall_for_both_assets(client, fixed_prices):
    """A conservative portfolio sitting in 100% cash: both VOO and BND
    are fully underweight, and — since $10,000 comfortably covers both
    shortfalls — neither buy is clamped by available cash."""
    db = SessionLocal()
    try:
        portfolio = _new_agent_portfolio(db, cash_balance=10_000.0)
        plan = plan_rebalance(db, portfolio, "conservative")

        plan = asyncio.run(plan)

        by_symbol = {item.symbol: item for item in plan}
        assert by_symbol["VOO"].skip_reason is None
        assert by_symbol["VOO"].buy_dollars == pytest.approx(6_000.0)  # 60% of $10,000
        assert by_symbol["BND"].skip_reason is None
        assert by_symbol["BND"].buy_dollars == pytest.approx(3_500.0)  # 35% of $10,000

        print(f"\nPlan (all cash): {[(i.symbol, i.current_weight, i.buy_dollars) for i in plan]}")
    finally:
        db.close()


def test_rebalance_portfolio_executes_buys_tagged_source_rebalance(client, fixed_prices):
    db = SessionLocal()
    try:
        portfolio = _new_agent_portfolio(db, cash_balance=10_000.0)
        portfolio_id = portfolio.id
        cash_before = portfolio.cash_balance

        trades = asyncio.run(rebalance_portfolio(db, portfolio, "conservative"))

        # Assert on relationship attributes (agent_decision) while the
        # session is still open — accessing them after db.close() would
        # trigger a lazy-load on a detached instance.
        assert len(trades) == 2
        assert {t.symbol for t in trades} == {"VOO", "BND"}
        assert all(t.source == "rebalance" for t in trades)
        assert all(t.status == "filled" for t in trades)
        assert all(t.agent_decision is None for t in trades)  # no AI rationale for a top-up
    finally:
        db.close()

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        expected_cash = cash_before - 6_000.0 - 3_500.0
        assert portfolio.cash_balance == pytest.approx(expected_cash)

        holdings = {h.symbol: h for h in portfolio.holdings}
        assert holdings["VOO"].quantity == pytest.approx(6_000.0 / FAKE_PRICES["VOO"])
        assert holdings["BND"].quantity == pytest.approx(3_500.0 / FAKE_PRICES["BND"])

        print(
            f"\nAfter rebalance: cash={portfolio.cash_balance:.2f} "
            f"VOO qty={holdings['VOO'].quantity} BND qty={holdings['BND'].quantity}"
        )
    finally:
        db.close()


def test_rebalance_skips_assets_within_tolerance(client, fixed_prices):
    """A portfolio already at (or within 2pp of) its target weights buys
    nothing — the whole point of the tolerance band."""
    db = SessionLocal()
    try:
        # $10,000 total: VOO $6,000 (60%) + BND $3,500 (35%) + $500 cash (5%)
        # — exactly on target for both.
        portfolio = _new_agent_portfolio(db, cash_balance=500.0)
        db.add(
            PortfolioHolding(
                portfolio_id=portfolio.id, symbol="VOO", asset_type="stock",
                quantity=12.0, avg_cost=500.0,  # 12 * 500 = $6,000
            )
        )
        db.add(
            PortfolioHolding(
                portfolio_id=portfolio.id, symbol="BND", asset_type="stock",
                quantity=43.75, avg_cost=80.0,  # 43.75 * 80 = $3,500
            )
        )
        db.commit()
        db.refresh(portfolio)


        trades = asyncio.run(rebalance_portfolio(db, portfolio, "conservative"))
    finally:
        db.close()

    assert trades == []


def test_rebalance_skips_entire_portfolio_when_available_cash_below_minimum(client, fixed_prices):
    db = SessionLocal()
    try:
        portfolio = _new_agent_portfolio(db, cash_balance=5.0)  # below MIN_TRADE_DOLLARS

        plan = asyncio.run(plan_rebalance(db, portfolio, "conservative"))
        trades = asyncio.run(rebalance_portfolio(db, portfolio, "conservative"))
    finally:
        db.close()

    assert all(item.skip_reason and "below the $10.00 minimum" in item.skip_reason for item in plan)
    assert trades == []


def test_pending_buy_notional_excluded_from_available_cash(client, fixed_prices):
    """The core requirement: a queued off-hours buy's estimated cost
    (quantity * current indicative price, since a pending row's stored
    price is a 0.0 placeholder) is subtracted from cash_balance before
    computing shortfalls — real numbers, shown in the assertions and
    printed for the report."""
    db = SessionLocal()
    try:
        portfolio = _new_agent_portfolio(db, cash_balance=10_000.0)
        portfolio_id = portfolio.id

        # 4 shares of VOO pending @ current price $500 = $2,000 earmarked.

        pending = queue_pending_trade(
            db, portfolio, "VOO", "stock", "buy", 4.0,
            datetime.utcnow() + timedelta(hours=2),
            source="agent",
        )
        assert pending.status == "pending"
    finally:
        db.close()

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)

        plan = asyncio.run(plan_rebalance(db, portfolio, "conservative"))
        by_symbol = {item.symbol: item for item in plan}

        pending_notional = 4.0 * FAKE_PRICES["VOO"]  # $2,000
        available_cash = 10_000.0 - pending_notional  # $8,000

        # VOO: 60% of total_value ($10,000, cash_balance unaffected by the
        # pending row) = $6,000, well under $8,000 available -> unclamped.
        assert by_symbol["VOO"].buy_dollars == pytest.approx(6_000.0)
        # BND: 35% of $10,000 = $3,500 target, but only $8,000 - $6,000 =
        # $2,000 of available cash remains after VOO's buy in this same
        # plan -> clamped to $2,000, not the full $3,500 shortfall.
        assert by_symbol["BND"].buy_dollars == pytest.approx(2_000.0)

        print(
            "\nPending-cash math: cash_balance=$10,000.00 "
            f"- pending_notional=${pending_notional:,.2f} (4 VOO @ $500) "
            f"= available_cash=${available_cash:,.2f}\n"
            f"VOO buy=${by_symbol['VOO'].buy_dollars:,.2f} (target $6,000.00, unclamped)\n"
            f"BND buy=${by_symbol['BND'].buy_dollars:,.2f} "
            f"(target $3,500.00, clamped to remaining $2,000.00 available cash)"
        )
    finally:
        db.close()


def test_cash_balance_matches_ledger_sum_with_rebalance_buys_in_the_mix(client, fixed_prices):
    """The cash ledger invariant (cash_balance == starting balance + sum
    of every CashLedgerEntry.amount) must still hold after rebalance
    buys — they go through apply_cash_delta the same as every other
    trade source."""
    db = SessionLocal()
    try:
        portfolio = _new_agent_portfolio(db, cash_balance=10_000.0)
        portfolio_id = portfolio.id
        cash_before = portfolio.cash_balance


        asyncio.run(rebalance_portfolio(db, portfolio, "conservative"))
    finally:
        db.close()

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        entries = (
            db.query(CashLedgerEntry)
            .filter(CashLedgerEntry.portfolio_id == portfolio_id)
            .all()
        )
        assert len(entries) == 2
        assert all(e.entry_type == "trade" for e in entries)
        assert cash_before + sum(e.amount for e in entries) == pytest.approx(
            portfolio.cash_balance
        )
    finally:
        db.close()
