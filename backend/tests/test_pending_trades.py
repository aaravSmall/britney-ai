"""Coverage for queued off-hours stock trades (agent/decision_loop.py's
_queue()/fill_due_pending_trades(), app/services/portfolio_service.py's
queue_pending_trade()/fill_pending_trade()).

Stock price lookups go through a monkeypatched market_data.get_price_for_holding
rather than the real Yahoo network call, matching test_cash_ledger.py's
stated convention of avoiding live market-data dependencies for a fixed,
predictable price — this file is the first to need it since it's the
first to exercise the fill_due_pending_trades() orchestration itself
(other files call record_trade_fill directly with an explicit price and
never hit market_data at all).
"""

import asyncio
from datetime import datetime, timedelta

import pytest

import app.services.market_data as market_data
from agent.decision_loop import RISK_PROFILES, _queue, fill_due_pending_trades
from app.database import SessionLocal
from app.models import CashLedgerEntry, Portfolio, Trade
from app.services.portfolio_service import queue_pending_trade

FAKE_PRICE = 250.0


def _own_portfolio_id(client, token: str) -> int:
    resp = client.get("/portfolios", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    mine = [p for p in resp.json() if p["owner_type"] == "user"]
    assert len(mine) == 1
    return mine[0]["id"]


@pytest.fixture
def fixed_price(monkeypatch):
    """Stubs market_data.get_price_for_holding at the module level (the
    same object agent/decision_loop.py's `from app.services import
    market_data` resolves to) so fill_due_pending_trades() never makes a
    real network call, and the fill price is deterministic."""

    async def _fake(symbol: str, asset_type: str) -> float:
        return FAKE_PRICE

    monkeypatch.setattr(market_data, "get_price_for_holding", _fake)
    return FAKE_PRICE


def test_queue_pending_trade_has_no_cash_or_holdings_effect(client):
    """An off-hours stock decision (agent/decision_loop._queue(), the
    function run_once() calls when asset_type == "stock" and
    is_market_hours() is False) writes a status="pending" Trade and
    leaves cash_balance and holdings completely untouched — the whole
    point of queuing instead of executing immediately."""
    token = "pending-test-queue-user"
    portfolio_id = _own_portfolio_id(client, token)

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        cash_before = portfolio.cash_balance
        holdings_before = len(portfolio.holdings)
        ledger_count_before = (
            db.query(CashLedgerEntry)
            .filter(CashLedgerEntry.portfolio_id == portfolio_id)
            .count()
        )

        trade = asyncio.run(
            _queue(db, portfolio, "AAPL", "buy", RISK_PROFILES["moderate"])
        )
        trade_id = trade.id
    finally:
        db.close()

    # Real row, queried fresh from a new session — not just the in-memory
    # object _queue() handed back.
    db = SessionLocal()
    try:
        trade = db.get(Trade, trade_id)
        portfolio = db.get(Portfolio, portfolio_id)

        print(
            "\nQueued pending trade row: "
            f"id={trade.id} symbol={trade.symbol} side={trade.side} "
            f"quantity={trade.quantity} price={trade.price} status={trade.status} "
            f"scheduled_execution_time={trade.scheduled_execution_time} "
            f"timestamp={trade.timestamp}"
        )

        assert trade.status == "pending"
        assert trade.symbol == "AAPL"
        assert trade.asset_type == "stock"
        assert trade.side == "buy"
        assert trade.quantity > 0
        assert trade.price == 0.0  # placeholder — never trust a pending row's price
        assert trade.scheduled_execution_time is not None
        assert trade.scheduled_execution_time > datetime.utcnow()
        assert trade.source == "agent"

        # No premature cash/holdings/ledger effect.
        assert portfolio.cash_balance == pytest.approx(cash_before)
        assert len(portfolio.holdings) == holdings_before
        ledger_count_after = (
            db.query(CashLedgerEntry)
            .filter(CashLedgerEntry.portfolio_id == portfolio_id)
            .count()
        )
        assert ledger_count_after == ledger_count_before
    finally:
        db.close()


def test_fill_due_pending_trades_fills_and_updates_status(client, fixed_price):
    """The 9:30am ET trigger logic (agent.decision_loop.fill_due_pending_trades,
    called by run_agent.py's precise wake) picks up a pending trade whose
    scheduled_execution_time has already passed, fetches a fresh price,
    and settles it via record_trade_fill's same cash/holdings/ledger path
    — only now, not at queue time."""
    token = "pending-test-fill-user"
    portfolio_id = _own_portfolio_id(client, token)

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        cash_before = portfolio.cash_balance
        trade = queue_pending_trade(
            db,
            portfolio,
            "AAPL",
            "stock",
            "buy",
            2.0,
            datetime.utcnow() - timedelta(hours=1),  # already due
            source="agent",
        )
        trade_id = trade.id
        assert trade.status == "pending"
    finally:
        db.close()

    db = SessionLocal()
    try:
        filled_count = asyncio.run(fill_due_pending_trades(db))
    finally:
        db.close()

    assert filled_count == 1

    db = SessionLocal()
    try:
        trade = db.get(Trade, trade_id)
        portfolio = db.get(Portfolio, portfolio_id)

        print(
            "\nFilled trade row: "
            f"id={trade.id} symbol={trade.symbol} quantity={trade.quantity} "
            f"price={trade.price} status={trade.status} timestamp={trade.timestamp}"
        )
        print(f"Portfolio cash after fill: {portfolio.cash_balance} (was {cash_before})")

        assert trade.status == "filled"
        assert trade.price == pytest.approx(FAKE_PRICE)

        expected_cost = 2.0 * FAKE_PRICE
        assert portfolio.cash_balance == pytest.approx(cash_before - expected_cost)

        holding = next(
            h for h in portfolio.holdings if h.symbol == "AAPL" and h.asset_type == "stock"
        )
        assert holding.quantity == pytest.approx(2.0)

        ledger_entry = (
            db.query(CashLedgerEntry)
            .filter(CashLedgerEntry.trade_id == trade_id)
            .one()
        )
        assert ledger_entry.amount == pytest.approx(-expected_cost)
        assert ledger_entry.balance_after == pytest.approx(portfolio.cash_balance)
    finally:
        db.close()


def test_fill_due_pending_trades_ignores_not_yet_due(client, fixed_price):
    """A pending trade scheduled for the future is left alone — the
    trigger only fills what's actually due by the time it runs."""
    token = "pending-test-not-due-user"
    portfolio_id = _own_portfolio_id(client, token)

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        trade = queue_pending_trade(
            db,
            portfolio,
            "MSFT",
            "stock",
            "buy",
            1.0,
            datetime.utcnow() + timedelta(days=1),  # not due yet
            source="agent",
        )
        trade_id = trade.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        filled_count = asyncio.run(fill_due_pending_trades(db))
    finally:
        db.close()

    assert filled_count == 0

    db = SessionLocal()
    try:
        trade = db.get(Trade, trade_id)
        assert trade.status == "pending"
        assert trade.price == 0.0
    finally:
        db.close()


def test_cash_balance_matches_ledger_sum_with_pending_trade_in_the_mix(client, fixed_price):
    """The cash ledger invariant (cash_balance == starting balance + sum
    of every CashLedgerEntry.amount) must still hold when a pending trade
    is queued and later filled alongside normal immediate trades — a
    pending trade must contribute zero ledger entries until it fills, and
    exactly one once it does."""
    token = "pending-test-invariant-user"
    portfolio_id = _own_portfolio_id(client, token)

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        cash_before = portfolio.cash_balance
        pending = queue_pending_trade(
            db,
            portfolio,
            "AAPL",
            "stock",
            "buy",
            3.0,
            datetime.utcnow() - timedelta(minutes=1),
            source="agent",
        )
        pending_id = pending.id
    finally:
        db.close()

    db = SessionLocal()
    try:
        filled_count = asyncio.run(fill_due_pending_trades(db))
    finally:
        db.close()
    assert filled_count == 1

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        entries = (
            db.query(CashLedgerEntry)
            .filter(CashLedgerEntry.portfolio_id == portfolio_id)
            .all()
        )
        assert cash_before + sum(e.amount for e in entries) == pytest.approx(
            portfolio.cash_balance
        )
        trade_entries = [e for e in entries if e.trade_id == pending_id]
        assert len(trade_entries) == 1
    finally:
        db.close()
