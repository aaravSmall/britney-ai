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
import app.services.rebalance_service as rebalance_service
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
    """Locks in the actual numbers post obvious/non-obvious split: VOO
    54% / BND 31.5% (90% of the 95% invested envelope, in their original
    60:35 relative proportion), non_obvious left as an empty per-cycle
    placeholder (populated dynamically, see effective_target_weights()),
    5% implied cash, no crypto entry at all."""
    targets = REBALANCE_TARGET_WEIGHTS["conservative"]
    assert targets["obvious"] == {"VOO": pytest.approx(0.540), "BND": pytest.approx(0.315)}
    assert targets["non_obvious"] == {}
    assert sum(targets["obvious"].values()) == pytest.approx(0.855)  # 90% of 95% invested
    assert "BTC" not in targets["obvious"]


def test_effective_target_weights_defaults_reproduce_pre_classification_behavior():
    """With no obvious_pass/non_obvious_routed passed at all (the default),
    effective_target_weights() must reproduce exactly what
    REBALANCE_TARGET_WEIGHTS[tier]["obvious"] says — the safe fallback for
    a cycle where classification hasn't run yet."""
    targets = rebalance_service.effective_target_weights("conservative")
    assert targets == {"VOO": pytest.approx(0.540), "BND": pytest.approx(0.315)}


def test_effective_target_weights_excludes_a_ticker_that_fails_classification():
    """A fixed ticker not in obvious_pass is omitted entirely from the
    flat targets dict — the mechanism behind buy-only/no-forced-sell:
    plan_rebalance()'s shortfall loop never even considers it."""
    targets = rebalance_service.effective_target_weights(
        "conservative", obvious_pass={"VOO"}  # BND excluded
    )
    assert targets == {"VOO": pytest.approx(0.540)}
    assert "BND" not in targets


def test_effective_target_weights_routes_and_equal_weights_non_obvious():
    targets = rebalance_service.effective_target_weights(
        "conservative",
        non_obvious_routed=[("XYZ", 0.9), ("ABC", 0.5)],
    )
    non_obvious_envelope = 0.95 * 0.10  # (1 - 5% cash) * conservative's 10% non_obvious ratio
    assert targets["XYZ"] == pytest.approx(non_obvious_envelope / 2)
    assert targets["ABC"] == pytest.approx(non_obvious_envelope / 2)
    # Obvious bucket (default: both pass) is still present alongside it.
    assert targets["VOO"] == pytest.approx(0.540)
    assert targets["BND"] == pytest.approx(0.315)


def test_effective_target_weights_caps_non_obvious_at_max_concurrent():
    """Conservative's cap is 2 (agent_config.MAX_CONCURRENT_NON_OBVIOUS) —
    routing 3 candidates keeps only the 2 highest-confidence ones, and
    the envelope is split between just those 2, not diluted across 3."""
    targets = rebalance_service.effective_target_weights(
        "conservative",
        obvious_pass=set(),  # isolate the non-obvious math from the obvious bucket
        non_obvious_routed=[("LOW", 0.1), ("HIGH", 0.9), ("MID", 0.5)],
    )
    non_obvious_envelope = 0.95 * 0.10
    assert set(targets) == {"HIGH", "MID"}  # LOW (lowest confidence) dropped by the cap
    assert targets["HIGH"] == pytest.approx(non_obvious_envelope / 2)
    assert targets["MID"] == pytest.approx(non_obvious_envelope / 2)


def test_effective_target_weights_zero_non_obvious_candidates_leaves_envelope_unallocated():
    """Per docs/DISCOVERY_DESIGN.md §6: zero qualifying non-obvious
    candidates this cycle means that envelope's cash simply isn't
    targeted at all — it must NOT get folded into the obvious bucket."""
    targets = rebalance_service.effective_target_weights(
        "conservative", non_obvious_routed=[],
    )
    assert targets == {"VOO": pytest.approx(0.540), "BND": pytest.approx(0.315)}
    assert sum(targets.values()) == pytest.approx(0.855)  # NOT 0.855 + 0.095


def test_plan_rebalance_all_cash_buys_full_shortfall_for_both_assets(client, fixed_prices):
    """A conservative portfolio sitting in 100% cash: both VOO and BND
    are fully underweight, and — since $10,000 comfortably covers both
    shortfalls — neither buy is clamped by available cash. Uses the
    default obvious_pass/non_obvious_routed (classification hasn't run
    in this test), so targets are exactly REBALANCE_TARGET_WEIGHTS
    ["conservative"]["obvious"]."""
    db = SessionLocal()
    try:
        portfolio = _new_agent_portfolio(db, cash_balance=10_000.0)
        plan = plan_rebalance(db, portfolio, "conservative")

        plan = asyncio.run(plan)

        by_symbol = {item.symbol: item for item in plan}
        assert by_symbol["VOO"].skip_reason is None
        assert by_symbol["VOO"].buy_dollars == pytest.approx(5_400.0)  # 54% of $10,000
        assert by_symbol["BND"].skip_reason is None
        assert by_symbol["BND"].buy_dollars == pytest.approx(3_150.0)  # 31.5% of $10,000

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
        expected_cash = cash_before - 5_400.0 - 3_150.0
        assert portfolio.cash_balance == pytest.approx(expected_cash)

        holdings = {h.symbol: h for h in portfolio.holdings}
        assert holdings["VOO"].quantity == pytest.approx(5_400.0 / FAKE_PRICES["VOO"])
        assert holdings["BND"].quantity == pytest.approx(3_150.0 / FAKE_PRICES["BND"])

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
        # $10,000 total: VOO $5,400 (54%) + BND $3,150 (31.5%) + $1,450
        # cash (the remaining 14.5% = 5% cash target + the 9.5%
        # non_obvious envelope, unallocated here since nothing was
        # routed) — exactly on target for both obvious holdings.
        portfolio = _new_agent_portfolio(db, cash_balance=1_450.0)
        db.add(
            PortfolioHolding(
                portfolio_id=portfolio.id, symbol="VOO", asset_type="stock",
                quantity=10.8, avg_cost=500.0,  # 10.8 * 500 = $5,400
            )
        )
        db.add(
            PortfolioHolding(
                portfolio_id=portfolio.id, symbol="BND", asset_type="stock",
                quantity=39.375, avg_cost=80.0,  # 39.375 * 80 = $3,150
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

        # VOO: 54% of total_value ($10,000, cash_balance unaffected by the
        # pending row) = $5,400, well under $8,000 available -> unclamped.
        assert by_symbol["VOO"].buy_dollars == pytest.approx(5_400.0)
        # BND: 31.5% of $10,000 = $3,150 target, but only $8,000 - $5,400 =
        # $2,600 of available cash remains after VOO's buy in this same
        # plan -> clamped to $2,600, not the full $3,150 shortfall.
        assert by_symbol["BND"].buy_dollars == pytest.approx(2_600.0)

        print(
            "\nPending-cash math: cash_balance=$10,000.00 "
            f"- pending_notional=${pending_notional:,.2f} (4 VOO @ $500) "
            f"= available_cash=${available_cash:,.2f}\n"
            f"VOO buy=${by_symbol['VOO'].buy_dollars:,.2f} (target $5,400.00, unclamped)\n"
            f"BND buy=${by_symbol['BND'].buy_dollars:,.2f} "
            f"(target $3,150.00, clamped to remaining $2,600.00 available cash)"
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


def test_buy_only_a_ticker_that_fails_classification_gets_no_new_buy_and_is_never_sold(
    client, fixed_prices
):
    """The explicit design decision from this prompt: a ticker that was
    "obvious" yesterday (has an existing holding) and fails classification
    today must receive ZERO new buy activity for it AND zero sell
    activity — the existing position just sits there untouched. Constructs
    this by passing obvious_pass excluding BND (simulating "BND failed
    classification this cycle") on a portfolio that already holds BND
    from a prior cycle."""
    db = SessionLocal()
    try:
        portfolio = _new_agent_portfolio(db, cash_balance=10_000.0)
        # Simulates a pre-existing BND position from a prior cycle, back
        # when BND still passed classification.
        db.add(
            PortfolioHolding(
                portfolio_id=portfolio.id, symbol="BND", asset_type="stock",
                quantity=10.0, avg_cost=80.0,  # $800 existing position
            )
        )
        db.commit()
        db.refresh(portfolio)
        portfolio_id = portfolio.id
        bnd_qty_before = 10.0

        # BND excluded from obvious_pass -> "failed classification this cycle".
        trades = asyncio.run(
            rebalance_portfolio(db, portfolio, "conservative", obvious_pass={"VOO"})
        )
    finally:
        db.close()

    assert {t.symbol for t in trades} == {"VOO"}  # only VOO bought, never BND
    assert all(t.side == "buy" for t in trades)  # this job never sells, period

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        holdings = {h.symbol: h for h in portfolio.holdings}
        # BND's existing position is completely untouched — same quantity,
        # no sell trade of any kind exists for it.
        assert holdings["BND"].quantity == pytest.approx(bnd_qty_before)
        bnd_trades = (
            db.query(rebalance_service.Trade)
            .filter(
                rebalance_service.Trade.portfolio_id == portfolio_id,
                rebalance_service.Trade.symbol == "BND",
            )
            .all()
        )
        assert bnd_trades == []  # zero trade rows of ANY kind for BND this cycle

        print(
            f"\nBND held at {holdings['BND'].quantity} shares before and after "
            f"— {len(bnd_trades)} trade(s) recorded for it (0 buys, 0 sells)."
        )
    finally:
        db.close()


def test_non_obvious_routed_candidates_actually_get_bought(client, fixed_prices, monkeypatch):
    """End-to-end (not just effective_target_weights()'s unit-level math):
    a routed non-obvious candidate must actually receive a real buy Trade
    through rebalance_portfolio(), tagged source="rebalance" the same as
    an obvious buy, sized from the tier's non_obvious envelope."""
    monkeypatch.setitem(FAKE_PRICES, "XYZ", 50.0)

    async def _fake(symbol: str, asset_type: str) -> float:
        return FAKE_PRICES.get(symbol, 100.0)

    monkeypatch.setattr(market_data, "get_price_for_holding", _fake)

    db = SessionLocal()
    try:
        portfolio = _new_agent_portfolio(db, cash_balance=10_000.0)
        portfolio_id = portfolio.id

        trades = asyncio.run(
            rebalance_portfolio(
                db, portfolio, "conservative",
                obvious_pass=set(),  # isolate: only the non-obvious buy should fire
                non_obvious_routed=[("XYZ", 0.8)],
            )
        )
    finally:
        db.close()

    assert {t.symbol for t in trades} == {"XYZ"}
    assert trades[0].source == "rebalance"
    expected_dollars = 10_000.0 * (0.95 * 0.10)  # full non_obvious envelope, 1 candidate
    assert trades[0].quantity == pytest.approx(expected_dollars / 50.0)

    print(
        f"\nNon-obvious buy: {trades[0].quantity} XYZ @ $50.00 "
        f"(${trades[0].quantity * 50.0:,.2f} of the ${expected_dollars:,.2f} envelope)"
    )
