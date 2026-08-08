"""Coverage for the cash ledger refactor (app/services/cash_ledger.py)
and the new deposit/withdraw endpoints (app/routes/cash.py).

Deposit/withdraw go through the real HTTP layer (client fixture), same
convention as test_portfolio_auth.py/test_favorites.py, with their own
tokens so this file's rows never collide with those files'. Trade-side
ledger coverage calls record_trade_fill directly at the service layer
instead of POST /trading/trade, to avoid depending on a live
market-data network call for a fixed, predictable price —
test_stocks.py already covers that real network path elsewhere.
"""

import pytest

from app.database import SessionLocal
from app.models import CashLedgerEntry, Portfolio
from app.services.portfolio_service import record_trade_fill

USER_A_TOKEN = "cash-test-user-a"
USER_B_TOKEN = "cash-test-user-b"

STARTING_BALANCE = 10_000.0


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _own_portfolio_id(client, token: str) -> int:
    resp = client.get("/portfolios", headers=_auth(token))
    assert resp.status_code == 200
    mine = [p for p in resp.json() if p["owner_type"] == "user"]
    assert len(mine) == 1
    return mine[0]["id"]


def _an_agent_portfolio_id(client) -> int:
    resp = client.get("/portfolios")
    assert resp.status_code == 200
    agents = [p for p in resp.json() if p["owner_type"] == "agent"]
    assert len(agents) == 3
    return agents[0]["id"]


def _ledger_entries(portfolio_id: int) -> list[CashLedgerEntry]:
    db = SessionLocal()
    try:
        return (
            db.query(CashLedgerEntry)
            .filter(CashLedgerEntry.portfolio_id == portfolio_id)
            .order_by(CashLedgerEntry.id)
            .all()
        )
    finally:
        db.close()


def test_deposit_increases_cash_balance_and_creates_ledger_row(client):
    portfolio_id = _own_portfolio_id(client, USER_A_TOKEN)
    before = client.get(
        f"/portfolios/{portfolio_id}/summary", headers=_auth(USER_A_TOKEN)
    ).json()

    resp = client.post(
        f"/portfolios/{portfolio_id}/deposit",
        json={"amount": 500.0, "note": "test deposit"},
        headers=_auth(USER_A_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cash_balance"] == pytest.approx(before["cash_balance"] + 500.0)

    last = _ledger_entries(portfolio_id)[-1]
    assert last.entry_type == "deposit"
    assert last.amount == 500.0
    assert last.balance_after == pytest.approx(body["cash_balance"])
    assert last.trade_id is None
    assert last.note == "test deposit"


def test_withdraw_decreases_cash_balance_and_creates_matching_ledger_row(client):
    portfolio_id = _own_portfolio_id(client, USER_A_TOKEN)
    before = client.get(
        f"/portfolios/{portfolio_id}/summary", headers=_auth(USER_A_TOKEN)
    ).json()

    resp = client.post(
        f"/portfolios/{portfolio_id}/withdraw",
        json={"amount": 200.0},
        headers=_auth(USER_A_TOKEN),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["cash_balance"] == pytest.approx(before["cash_balance"] - 200.0)

    last = _ledger_entries(portfolio_id)[-1]
    assert last.entry_type == "withdrawal"
    assert last.amount == -200.0
    assert last.balance_after == pytest.approx(body["cash_balance"])


def test_withdraw_rejected_when_amount_exceeds_cash_balance(client):
    portfolio_id = _own_portfolio_id(client, USER_A_TOKEN)
    summary = client.get(
        f"/portfolios/{portfolio_id}/summary", headers=_auth(USER_A_TOKEN)
    ).json()
    too_much = summary["cash_balance"] + 1000.0

    resp = client.post(
        f"/portfolios/{portfolio_id}/withdraw",
        json={"amount": too_much},
        headers=_auth(USER_A_TOKEN),
    )
    assert resp.status_code == 400
    assert "Insufficient cash" in resp.json()["detail"]

    after = client.get(
        f"/portfolios/{portfolio_id}/summary", headers=_auth(USER_A_TOKEN)
    ).json()
    assert after["cash_balance"] == summary["cash_balance"]


@pytest.mark.parametrize("amount", [0, -10])
def test_deposit_amount_must_be_positive(client, amount):
    portfolio_id = _own_portfolio_id(client, USER_A_TOKEN)
    resp = client.post(
        f"/portfolios/{portfolio_id}/deposit",
        json={"amount": amount},
        headers=_auth(USER_A_TOKEN),
    )
    assert resp.status_code == 422


def test_deposit_rejected_on_agent_portfolio(client):
    portfolio_id = _an_agent_portfolio_id(client)
    resp = client.post(
        f"/portfolios/{portfolio_id}/deposit",
        json={"amount": 100.0},
        headers=_auth(USER_A_TOKEN),
    )
    assert resp.status_code == 403


def test_withdraw_rejected_on_agent_portfolio(client):
    portfolio_id = _an_agent_portfolio_id(client)
    resp = client.post(
        f"/portfolios/{portfolio_id}/withdraw",
        json={"amount": 100.0},
        headers=_auth(USER_A_TOKEN),
    )
    assert resp.status_code == 403


def test_deposit_requires_owner_token(client):
    portfolio_id = _own_portfolio_id(client, USER_A_TOKEN)
    resp = client.post(
        f"/portfolios/{portfolio_id}/deposit",
        json={"amount": 100.0},
        headers=_auth(USER_B_TOKEN),
    )
    assert resp.status_code == 403


def test_trade_fill_produces_matching_ledger_entries(client):
    """A buy then a sell each create one CashLedgerEntry(entry_type=
    "trade") whose amount matches the cash_balance delta it caused."""
    token = "cash-test-trade-user"
    portfolio_id = _own_portfolio_id(client, token)

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        cash_before_buy = portfolio.cash_balance
        buy_trade = record_trade_fill(
            db, portfolio, "AAPL", "stock", "buy", 2, 100.0, source="user"
        )
        buy_trade_id = buy_trade.id
        cash_after_buy = portfolio.cash_balance
        sell_trade = record_trade_fill(
            db, portfolio, "AAPL", "stock", "sell", 2, 110.0, source="user"
        )
        sell_trade_id = sell_trade.id
        cash_after_sell = portfolio.cash_balance
    finally:
        db.close()

    assert cash_after_buy == pytest.approx(cash_before_buy - 200.0)
    assert cash_after_sell == pytest.approx(cash_after_buy + 220.0)

    entries = [e for e in _ledger_entries(portfolio_id) if e.entry_type == "trade"]
    assert len(entries) == 2
    buy_entry, sell_entry = entries
    assert buy_entry.trade_id == buy_trade_id
    assert buy_entry.amount == -200.0
    assert buy_entry.balance_after == pytest.approx(cash_after_buy)
    assert sell_entry.trade_id == sell_trade_id
    assert sell_entry.amount == 220.0
    assert sell_entry.balance_after == pytest.approx(cash_after_sell)


def test_cash_balance_matches_ledger_sum_across_mixed_activity(client):
    """Invariant: cash_balance == starting balance + sum of every
    CashLedgerEntry.amount for that portfolio — checked against a
    portfolio put through a real mix of trades, a deposit, and a
    withdrawal, on the same persistent test DB the rest of this suite
    writes to (not an isolated fresh one)."""
    token = "cash-test-invariant-user"
    portfolio_id = _own_portfolio_id(client, token)

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        record_trade_fill(db, portfolio, "AAPL", "stock", "buy", 3, 150.0, source="user")
        record_trade_fill(db, portfolio, "AAPL", "stock", "sell", 1, 160.0, source="user")
        record_trade_fill(db, portfolio, "BTC", "crypto", "buy", 0.01, 95_000.0, source="user")
    finally:
        db.close()

    assert (
        client.post(
            f"/portfolios/{portfolio_id}/deposit",
            json={"amount": 1000.0},
            headers=_auth(token),
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/portfolios/{portfolio_id}/withdraw",
            json={"amount": 250.0},
            headers=_auth(token),
        ).status_code
        == 200
    )

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        entries = (
            db.query(CashLedgerEntry)
            .filter(CashLedgerEntry.portfolio_id == portfolio_id)
            .all()
        )
        assert len(entries) == 5  # 3 trades + 1 deposit + 1 withdrawal
        assert STARTING_BALANCE + sum(e.amount for e in entries) == pytest.approx(
            portfolio.cash_balance
        )
    finally:
        db.close()
