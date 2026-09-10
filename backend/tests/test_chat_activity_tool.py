"""Coverage for the "why did I lose money on <date>" chat capability:
app.services.performance_service.portfolio_activity_report() (real data
behind the answer) and app.ai.chat_engine.chat_reply()'s tool-calling
loop that calls it.

chat_reply()'s OpenAI client is mocked entirely (a real call would be
billed, same reasoning as test_agent_dedup.py) — this file is scoped to
chat_engine.py's own orchestration (does it call the tool when the model
asks, does it feed the result back, does it return the model's final
text), not to OpenAI's behavior or to portfolio_activity_report()'s own
correctness, which the report-level test below covers directly with a
real DB session and a mocked stock_data.history() (no live network call).
"""

import asyncio
import json
from datetime import datetime, timedelta

import pytest

import app.ai.chat_engine as chat_engine
import app.services.performance_service as performance_service
from app.database import SessionLocal
from app.models import User
from app.services.portfolio_service import get_or_create_portfolio, record_trade_fill

# ---------------------------------------------------------------------
# portfolio_activity_report() — real DB session, mocked price history.
# ---------------------------------------------------------------------


def test_activity_report_includes_trades_ledger_and_price_move(client, monkeypatch):
    # Relative to "now" (5 days back), not a fixed calendar date — the
    # report's default window is [utcnow() - 14 days, utcnow()], so a
    # hardcoded date eventually rolls out of range as real time passes
    # (this is exactly what broke here: a candle pinned to 2026-08-19
    # fell outside the window once "now" moved past 2026-09-02). 5 days
    # keeps it safely inside the 14-day window regardless of when the
    # suite runs.
    candle_timestamp = (datetime.utcnow() - timedelta(days=5)).strftime("%Y-%m-%dT00:00:00+00:00")

    async def _fake_history(ticker, range_key):
        return [
            {
                "timestamp": candle_timestamp,
                "open": 100.0,
                "high": 101.0,
                "low": 98.0,
                "close": 95.0,
                "volume": 1000,
            }
        ]

    monkeypatch.setattr(performance_service.stock_data, "history", _fake_history)

    db = SessionLocal()
    try:
        user = User(firebase_uid="activity-report-test", email="activity-report-test@example.com")
        db.add(user)
        db.commit()
        db.refresh(user)

        portfolio = get_or_create_portfolio(db, user)
        trade = record_trade_fill(
            db, portfolio, "AAPL", "stock", "buy", 1.0, 313.33, source="user"
        )

        report = asyncio.run(performance_service.portfolio_activity_report(db, portfolio))
    finally:
        db.close()

    assert "BUY 1.0 AAPL @ $313.33" in report
    assert trade.symbol in report
    # The mocked candle's -5% move should show up in the per-holding section.
    assert "-5.00%" in report or "95.00" in report


def test_activity_report_says_so_when_no_snapshots_exist(client, monkeypatch):
    async def _fake_history(ticker, range_key):
        return []

    monkeypatch.setattr(performance_service.stock_data, "history", _fake_history)

    db = SessionLocal()
    try:
        user = User(firebase_uid="activity-report-empty-test", email="activity-report-empty-test@example.com")
        db.add(user)
        db.commit()
        db.refresh(user)
        portfolio = get_or_create_portfolio(db, user)

        report = asyncio.run(performance_service.portfolio_activity_report(db, portfolio))
    finally:
        db.close()

    assert "No trades filled in this window." in report
    assert "No portfolio value history recorded" in report


# ---------------------------------------------------------------------
# chat_reply()'s tool-calling loop — OpenAI client mocked, portfolio_
# activity_report() mocked to a spy so this stays scoped to chat_
# engine.py's own orchestration.
# ---------------------------------------------------------------------


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, call_id, name, arguments):
        self.id = call_id
        self.function = _FakeFunction(name, arguments)

    def model_dump(self):
        return {
            "id": self.id,
            "type": "function",
            "function": {"name": self.function.name, "arguments": self.function.arguments},
        }


class _FakeMessage:
    def __init__(self, content=None, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class _FakeCompletions:
    def __init__(self, messages):
        self._messages = list(messages)
        self.create_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        message = self._messages.pop(0)

        class _Resp:
            choices = [type("Choice", (), {"message": message})()]

        return _Resp()


class _FakeClient:
    def __init__(self, messages):
        completions = _FakeCompletions(messages)
        self.chat = type("Chat", (), {"completions": completions})()


class _FakeSettings:
    openai_api_key = "fake-key-for-test"


def test_chat_reply_calls_activity_tool_for_a_performance_question(client, monkeypatch):
    tool_call = _FakeToolCall(
        "call_1", "get_portfolio_activity", json.dumps({"start_date": "2026-08-18", "end_date": "2026-08-20"})
    )
    fake_client = _FakeClient(
        [
            _FakeMessage(content=None, tool_calls=[tool_call]),
            _FakeMessage(content="You lost $51.50 to a real market dip, not a bug.", tool_calls=None),
        ]
    )

    report_calls = []

    async def _fake_report(db, portfolio, start_date=None, end_date=None):
        report_calls.append((start_date, end_date))
        return "Activity report: SPY dropped $51.50 at market open, no trade behind it."

    monkeypatch.setattr(chat_engine, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(chat_engine, "OpenAI", lambda api_key: fake_client)
    monkeypatch.setattr(chat_engine, "portfolio_activity_report", _fake_report)

    db = SessionLocal()
    try:
        user = User(firebase_uid="chat-tool-test", email="chat-tool-test@example.com")
        db.add(user)
        db.commit()
        db.refresh(user)
        portfolio = get_or_create_portfolio(db, user)

        reply = asyncio.run(
            chat_reply_wrapper(
                db, user, portfolio, "No holdings yet.", [{"role": "user", "content": "why did I lose $50 on aug 19?"}]
            )
        )
    finally:
        db.close()

    # The tool was actually invoked, with the args the model asked for.
    assert report_calls == [("2026-08-18", "2026-08-20")]
    # The final answer is the model's second response, grounded in the
    # tool's result (which was fed back as a "tool" message).
    assert reply == "You lost $51.50 to a real market dip, not a bug."
    assert len(fake_client.chat.completions.create_calls) == 2
    second_call_messages = fake_client.chat.completions.create_calls[1]["messages"]
    assert any(m.get("role") == "tool" and "51.50" in m.get("content", "") for m in second_call_messages)


def chat_reply_wrapper(db, user, portfolio, summary, messages):
    return chat_engine.chat_reply(db, user, portfolio, summary, messages)


def test_chat_reply_returns_directly_when_model_skips_the_tool(client, monkeypatch):
    fake_client = _FakeClient([_FakeMessage(content="Diversified ETFs are a solid long-term core.", tool_calls=None)])

    async def _unexpected_report(*args, **kwargs):
        pytest.fail("portfolio_activity_report should not be called for a non-performance question")

    monkeypatch.setattr(chat_engine, "get_settings", lambda: _FakeSettings())
    monkeypatch.setattr(chat_engine, "OpenAI", lambda api_key: fake_client)
    monkeypatch.setattr(chat_engine, "portfolio_activity_report", _unexpected_report)

    db = SessionLocal()
    try:
        user = User(firebase_uid="chat-tool-skip-test", email="chat-tool-skip-test@example.com")
        db.add(user)
        db.commit()
        db.refresh(user)
        portfolio = get_or_create_portfolio(db, user)

        reply = asyncio.run(
            chat_reply_wrapper(
                db, user, portfolio, "No holdings yet.", [{"role": "user", "content": "what's a good ETF?"}]
            )
        )
    finally:
        db.close()

    assert reply == "Diversified ETFs are a solid long-term core."
    assert len(fake_client.chat.completions.create_calls) == 1
