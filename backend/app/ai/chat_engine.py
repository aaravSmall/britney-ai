"""Context-aware chat using OpenAI with user + portfolio summary."""

from openai import OpenAI
from starlette.concurrency import run_in_threadpool

from app.config import get_settings
from app.models import User


async def chat_reply(
    user: User,
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
    api_messages = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] in ("user", "assistant"):
            api_messages.append({"role": m["role"], "content": m["content"]})

    # The OpenAI SDK's default client is sync (blocking network I/O); this
    # route handler is async def, so a direct call here would block the
    # whole event loop — and every other concurrent request — for the
    # duration of the API call.
    resp = await run_in_threadpool(
        client.chat.completions.create,
        model="gpt-4o-mini",
        messages=api_messages,
        temperature=0.5,
    )
    return (resp.choices[0].message.content or "").strip()
