"""Coverage for recurring auto-invest schedules: the is_due()/
execute_schedule() service logic (app/services/auto_invest_service.py),
the CRUD endpoints (app/routes/auto_invest.py), and run_auto_invest.py's
per-cycle loop isolation (one schedule's failure doesn't block others).

is_due() tests construct AutoInvestSchedule instances in memory (never
added to a session) — it's a pure function over a few attributes, no DB
needed. CRUD tests go through the real HTTP layer (client fixture),
same convention as test_cash_ledger.py/test_favorites.py. Execution
tests call the async service/script functions directly via
asyncio.run() (no pytest-asyncio in this suite) against a real ticker's
live price — same "real network call, loose structural assertions"
approach test_stocks.py/test_cash_ledger.py already use elsewhere.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from app.database import SessionLocal
from app.models import AutoInvestSchedule, CashLedgerEntry, Trade
from app.services.auto_invest_service import execute_schedule, is_due
from app.services.portfolio_service import InsufficientFundsError
from agent.run_auto_invest import _run_cycle

USER_A_TOKEN = "auto-invest-test-user-a"
USER_B_TOKEN = "auto-invest-test-user-b"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _own_portfolio_id(client, token: str) -> int:
    resp = client.get("/portfolios", headers=_auth(token))
    assert resp.status_code == 200
    mine = [p for p in resp.json() if p["owner_type"] == "user"]
    assert len(mine) == 1
    return mine[0]["id"]


# --- is_due() -----------------------------------------------------------


def _schedule(*, enabled=True, last_executed_at=None, interval="daily"):
    return AutoInvestSchedule(
        user_id=1,
        portfolio_id=1,
        ticker="AAPL",
        asset_type="stock",
        amount=50.0,
        interval=interval,
        enabled=enabled,
        last_executed_at=last_executed_at,
    )


def test_is_due_never_executed_is_always_due():
    assert is_due(_schedule(last_executed_at=None)) is True


def test_is_due_disabled_is_never_due():
    schedule = _schedule(enabled=False, last_executed_at=None)
    assert is_due(schedule) is False


@pytest.mark.parametrize(
    "interval,hours_ago,expected",
    [
        ("daily", 1, False),
        ("daily", 25, True),
        ("weekly", 24, False),
        ("weekly", 24 * 8, True),
        ("monthly", 24 * 10, False),
        ("monthly", 24 * 31, True),
    ],
)
def test_is_due_interval_windows(interval, hours_ago, expected):
    now = datetime(2026, 1, 15, 12, 0, 0)
    schedule = _schedule(interval=interval, last_executed_at=now - timedelta(hours=hours_ago))
    assert is_due(schedule, now=now) is expected


# --- CRUD endpoints -------------------------------------------------------


def test_create_schedule(client):
    resp = client.post(
        "/auto-invest/schedules",
        json={"ticker": "voo", "amount": 25.0, "interval": "weekly"},
        headers=_auth(USER_A_TOKEN),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["ticker"] == "VOO"
    assert body["asset_type"] == "stock"
    assert body["amount"] == 25.0
    assert body["interval"] == "weekly"
    assert body["enabled"] is True
    assert body["last_executed_at"] is None


def test_create_schedule_rejects_bad_interval(client):
    resp = client.post(
        "/auto-invest/schedules",
        json={"ticker": "VOO", "amount": 25.0, "interval": "yearly"},
        headers=_auth(USER_A_TOKEN),
    )
    assert resp.status_code == 422


def test_create_schedule_rejects_nonpositive_amount(client):
    resp = client.post(
        "/auto-invest/schedules",
        json={"ticker": "VOO", "amount": 0, "interval": "daily"},
        headers=_auth(USER_A_TOKEN),
    )
    assert resp.status_code == 422


def test_list_schedules_scoped_to_owner(client):
    client.post(
        "/auto-invest/schedules",
        json={"ticker": "AAPL", "amount": 10.0, "interval": "daily"},
        headers=_auth(USER_A_TOKEN),
    )
    client.post(
        "/auto-invest/schedules",
        json={"ticker": "MSFT", "amount": 10.0, "interval": "daily"},
        headers=_auth(USER_B_TOKEN),
    )
    resp_a = client.get("/auto-invest/schedules", headers=_auth(USER_A_TOKEN))
    resp_b = client.get("/auto-invest/schedules", headers=_auth(USER_B_TOKEN))
    assert resp_a.status_code == 200
    assert resp_b.status_code == 200
    tickers_a = {s["ticker"] for s in resp_a.json()}
    tickers_b = {s["ticker"] for s in resp_b.json()}
    assert "AAPL" in tickers_a and "MSFT" not in tickers_a
    assert "MSFT" in tickers_b and "AAPL" not in tickers_b


def test_update_schedule_toggles_enabled_and_amount(client):
    created = client.post(
        "/auto-invest/schedules",
        json={"ticker": "NVDA", "amount": 15.0, "interval": "daily"},
        headers=_auth(USER_A_TOKEN),
    ).json()

    resp = client.patch(
        f"/auto-invest/schedules/{created['id']}",
        json={"enabled": False, "amount": 40.0},
        headers=_auth(USER_A_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert body["amount"] == 40.0
    assert body["interval"] == "daily"  # untouched


def test_update_other_users_schedule_is_404(client):
    created = client.post(
        "/auto-invest/schedules",
        json={"ticker": "TSLA", "amount": 15.0, "interval": "daily"},
        headers=_auth(USER_A_TOKEN),
    ).json()

    resp = client.patch(
        f"/auto-invest/schedules/{created['id']}",
        json={"enabled": False},
        headers=_auth(USER_B_TOKEN),
    )
    assert resp.status_code == 404


def test_delete_schedule(client):
    created = client.post(
        "/auto-invest/schedules",
        json={"ticker": "AMD", "amount": 15.0, "interval": "daily"},
        headers=_auth(USER_A_TOKEN),
    ).json()

    resp = client.delete(f"/auto-invest/schedules/{created['id']}", headers=_auth(USER_A_TOKEN))
    assert resp.status_code == 204

    listed = client.get("/auto-invest/schedules", headers=_auth(USER_A_TOKEN)).json()
    assert created["id"] not in [s["id"] for s in listed]


def test_delete_other_users_schedule_is_404(client):
    created = client.post(
        "/auto-invest/schedules",
        json={"ticker": "AMD", "amount": 15.0, "interval": "daily"},
        headers=_auth(USER_A_TOKEN),
    ).json()

    resp = client.delete(f"/auto-invest/schedules/{created['id']}", headers=_auth(USER_B_TOKEN))
    assert resp.status_code == 404


# --- execute_schedule() ---------------------------------------------------


def test_execute_schedule_success_creates_trade_and_ledger_entry(client):
    token = "auto-invest-test-exec-user"
    portfolio_id = _own_portfolio_id(client, token)
    created = client.post(
        "/auto-invest/schedules",
        json={"ticker": "AAPL", "amount": 50.0, "interval": "daily"},
        headers=_auth(token),
    ).json()

    db = SessionLocal()
    try:
        schedule = db.get(AutoInvestSchedule, created["id"])
        assert schedule.last_executed_at is None
        trade = asyncio.run(execute_schedule(db, schedule))
        trade_id = trade.id
        assert trade.source == "auto_invest"
        assert trade.side == "buy"
        assert trade.symbol == "AAPL"

        db.refresh(schedule)
        assert schedule.last_executed_at is not None

        ledger_entry = (
            db.query(CashLedgerEntry)
            .filter(CashLedgerEntry.trade_id == trade_id)
            .one()
        )
        assert ledger_entry.entry_type == "trade"
        assert ledger_entry.amount < 0  # a buy debits cash
    finally:
        db.close()

    summary = client.get(f"/portfolios/{portfolio_id}/summary", headers=_auth(token)).json()
    assert any(h["symbol"] == "AAPL" for h in summary["holdings"])


def test_execute_schedule_insufficient_funds_does_not_update_last_executed_at(client):
    token = "auto-invest-test-poor-user"
    _own_portfolio_id(client, token)  # creates the portfolio at its default $10k
    created = client.post(
        "/auto-invest/schedules",
        json={"ticker": "AAPL", "amount": 999_999.0, "interval": "daily"},
        headers=_auth(token),
    ).json()

    db = SessionLocal()
    try:
        schedule = db.get(AutoInvestSchedule, created["id"])
        with pytest.raises(InsufficientFundsError):
            asyncio.run(execute_schedule(db, schedule))
        db.refresh(schedule)
        assert schedule.last_executed_at is None
    finally:
        db.close()


def test_run_cycle_one_failure_does_not_block_other_due_schedules(client):
    """A too-expensive schedule failing with InsufficientFundsError must
    not stop _run_cycle() from still executing a different, affordable,
    due schedule in the same pass."""
    poor_token = "auto-invest-test-cycle-poor"
    ok_token = "auto-invest-test-cycle-ok"
    _own_portfolio_id(client, poor_token)
    _own_portfolio_id(client, ok_token)

    failing = client.post(
        "/auto-invest/schedules",
        json={"ticker": "AAPL", "amount": 999_999.0, "interval": "daily"},
        headers=_auth(poor_token),
    ).json()
    succeeding = client.post(
        "/auto-invest/schedules",
        json={"ticker": "AAPL", "amount": 25.0, "interval": "daily"},
        headers=_auth(ok_token),
    ).json()

    asyncio.run(_run_cycle())

    db = SessionLocal()
    try:
        failing_row = db.get(AutoInvestSchedule, failing["id"])
        succeeding_row = db.get(AutoInvestSchedule, succeeding["id"])
        assert failing_row.last_executed_at is None
        assert succeeding_row.last_executed_at is not None

        trade = (
            db.query(Trade)
            .filter(Trade.portfolio_id == succeeding_row.portfolio_id, Trade.source == "auto_invest")
            .one()
        )
        assert trade.symbol == "AAPL"
    finally:
        db.close()
