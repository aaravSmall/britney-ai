"""Coverage for POST /trading/trade — previously untested at the HTTP
layer (test_cash_ledger.py calls record_trade_fill directly instead, to
avoid a live market-data network call). Adding real coverage now that
this route has a second branch: an off-hours stock order queues instead
of filling, mirroring agent/decision_loop.py's existing rule.

market_data.get_price_for_holding is monkeypatched for a deterministic
price without a live Yahoo call (same convention test_pending_trades.py/
test_rebalance.py established). is_market_hours is monkeypatched on
app.routes.trading's own imported reference (not agent.market_hours
itself) so each test controls whether "now" is off-hours without
depending on the real wall clock.
"""

import json

import app.routes.trading as trading_route
import app.services.market_data as market_data
from app.database import SessionLocal
from app.models import CashLedgerEntry, Portfolio, Trade

FAKE_PRICE = 250.0


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _own_portfolio_id(client, token: str) -> int:
    resp = client.get("/portfolios", headers=_auth(token))
    assert resp.status_code == 200
    mine = [p for p in resp.json() if p["owner_type"] == "user"]
    assert len(mine) == 1
    return mine[0]["id"]


async def _fake_price(symbol: str, asset_type: str) -> float:
    return FAKE_PRICE


def test_off_hours_stock_buy_queues_instead_of_filling(client, monkeypatch):
    monkeypatch.setattr(market_data, "get_price_for_holding", _fake_price)
    monkeypatch.setattr(trading_route, "is_market_hours", lambda: False)

    token = "trading-route-off-hours-user"
    portfolio_id = _own_portfolio_id(client, token)

    db = SessionLocal()
    try:
        cash_before = db.get(Portfolio, portfolio_id).cash_balance
    finally:
        db.close()

    resp = client.post(
        "/trading/trade",
        json={"symbol": "AAPL", "asset_type": "stock", "side": "buy", "quantity": 2},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pending"
    assert "queued" in body["message"].lower()
    assert "9:30am ET" in body["message"]

    db = SessionLocal()
    try:
        portfolio = db.get(Portfolio, portfolio_id)
        # No premature cash effect — the whole point of queuing.
        assert portfolio.cash_balance == cash_before

        trade = (
            db.query(Trade)
            .filter(Trade.portfolio_id == portfolio_id, Trade.source == "user")
            .order_by(Trade.id.desc())
            .first()
        )
        assert trade is not None
        assert trade.status == "pending"
        assert trade.symbol == "AAPL"
        assert trade.quantity == 2
        assert trade.scheduled_execution_time is not None

        ledger_count = (
            db.query(CashLedgerEntry)
            .filter(CashLedgerEntry.portfolio_id == portfolio_id)
            .count()
        )
    finally:
        db.close()

    # No ledger entry either — matches the agent's own queue_pending_trade path.
    assert ledger_count == 0

    print(f"\nQueued order response: {json.dumps(body, indent=2)}")


def test_during_hours_stock_buy_still_fills_immediately(client, monkeypatch):
    """Regression check: the existing immediate-fill path (during market
    hours) is unchanged by adding the off-hours branch."""
    monkeypatch.setattr(market_data, "get_price_for_holding", _fake_price)
    monkeypatch.setattr(trading_route, "is_market_hours", lambda: True)

    token = "trading-route-during-hours-user"
    portfolio_id = _own_portfolio_id(client, token)

    resp = client.post(
        "/trading/trade",
        json={"symbol": "AAPL", "asset_type": "stock", "side": "buy", "quantity": 2},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "filled"

    db = SessionLocal()
    try:
        trade = (
            db.query(Trade)
            .filter(Trade.portfolio_id == portfolio_id, Trade.source == "user")
            .order_by(Trade.id.desc())
            .first()
        )
        assert trade.status == "filled"
        assert trade.price == FAKE_PRICE
        assert trade.scheduled_execution_time is None
    finally:
        db.close()


def test_off_hours_crypto_buy_still_fills_immediately(client, monkeypatch):
    """Crypto is unaffected by the off-hours queuing rule — always fills,
    same as agent/decision_loop.py's own crypto handling."""
    monkeypatch.setattr(market_data, "get_price_for_holding", _fake_price)
    monkeypatch.setattr(trading_route, "is_market_hours", lambda: False)

    token = "trading-route-crypto-off-hours-user"
    portfolio_id = _own_portfolio_id(client, token)

    resp = client.post(
        "/trading/trade",
        json={"symbol": "BTC", "asset_type": "crypto", "side": "buy", "quantity": 0.01},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "filled"

    db = SessionLocal()
    try:
        trade = (
            db.query(Trade)
            .filter(Trade.portfolio_id == portfolio_id, Trade.symbol == "BTC")
            .order_by(Trade.id.desc())
            .first()
        )
        assert trade.status == "filled"
        assert trade.scheduled_execution_time is None
    finally:
        db.close()
