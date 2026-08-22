"""Context-aware chat using OpenAI with user + portfolio summary.

Handles "why did I lose money on <date>" style questions via a single
OpenAI tool, get_portfolio_activity, backed by
app.services.performance_service.portfolio_activity_report() — real
trades, cash movements, snapshot history, and per-symbol price moves,
rather than letting the model guess at what happened from the static
holdings summary alone (which has no date information in it at all)."""

import json

from openai import OpenAI
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.models import Portfolio, User
from app.services.performance_service import portfolio_activity_report

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_portfolio_activity",
            "description": (
                "Look up this portfolio's real trades, cash deposits/withdrawals, "
                "recorded value history, and per-holding price moves for a date range. "
                "Call this before answering any question about past performance, gains, "
                "or losses (e.g. 'why did I lose money on Aug 19') instead of guessing — "
                "the answer must come from real data, not a plausible-sounding guess."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD), inclusive. Defaults to 14 days before end_date.",
                    },
                    "end_date": {
                        "type": "string",
                        "description": "ISO date (YYYY-MM-DD), inclusive. Defaults to now.",
                    },
                },
            },
        },
    }
]

_MAX_TOOL_ROUNDS = 3


async def chat_reply(
    db: Session,
    user: User,
    portfolio: Portfolio,
    portfolio_summary: str,
    messages: list[dict[str, str]],
) -> str:
    settings = get_settings()
    system = f"""You are britney.ai, a supportive investing assistant (not a licensed advisor).
User email: {user.email}
Risk tolerance: {user.risk_tolerance or 'not set'}
Goals: {user.investment_goals or 'not set'}
Horizon: {user.time_horizon or 'not set'}
Auto-invest (paper): {user.auto_invest_enabled}
Portfolio summary:
{portfolio_summary}
Answer clearly and simply. If asked for specific trades, remind this is educational.
For any question about past performance, a specific date, or why the portfolio gained or
lost money, call get_portfolio_activity to look up what actually happened before answering
— if it shows no trades and no recorded value history for that window, say so plainly
rather than inventing a plausible-sounding cause. Call it at most once per question: if the
user names a date or range, pass it in start_date/end_date; if they don't, omit both and use
the default window rather than guessing a date yourself.
"""

    if not settings.openai_api_key:
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"),
            "",
        )
        return (
            f"[Offline mode — add OPENAI_API_KEY for full AI] "
            f"You said: {last_user[:200]}. "
            f"Based on your profile (risk: {user.risk_tolerance}), "
            "consider diversified ETFs like VOO or SPY for long-term growth, "
            "and keep crypto to a small slice unless you accept volatility."
        )

    client = OpenAI(api_key=settings.openai_api_key)
    api_messages: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] in ("user", "assistant"):
            api_messages.append({"role": m["role"], "content": m["content"]})

    # The OpenAI SDK's default client is sync (blocking network I/O); this
    # route handler is async def, so a direct call here would block the
    # whole event loop — and every other concurrent request — for the
    # duration of the API call.
    for _ in range(_MAX_TOOL_ROUNDS):
        resp = await run_in_threadpool(
            client.chat.completions.create,
            model="gpt-4o-mini",
            messages=api_messages,
            tools=_TOOLS,
            temperature=0.5,
        )
        choice = resp.choices[0].message
        if not choice.tool_calls:
            return (choice.content or "").strip()

        api_messages.append(
            {
                "role": "assistant",
                "content": choice.content,
                "tool_calls": [tc.model_dump() for tc in choice.tool_calls],
            }
        )
        for tool_call in choice.tool_calls:
            args = json.loads(tool_call.function.arguments or "{}")
            report = await portfolio_activity_report(
                db, portfolio, args.get("start_date"), args.get("end_date")
            )
            api_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": report,
                }
            )

    # Ran out of tool rounds (shouldn't normally happen) — ask once more
    # without tools so the model is forced to answer from what it has.
    resp = await run_in_threadpool(
        client.chat.completions.create,
        model="gpt-4o-mini",
        messages=api_messages,
        temperature=0.5,
    )
    return (resp.choices[0].message.content or "").strip()
