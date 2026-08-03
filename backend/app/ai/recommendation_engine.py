"""
AI recommendation: OpenAI when configured; structured mock fallback for dev.
"""

import json
import re
from typing import Any

from openai import OpenAI

from app.config import get_settings
from app.schemas.recommendation import (
    RecommendationResponse,
    RecommendedAsset,
)


SYSTEM = """You are britney.ai, a friendly investing coach for beginners.
Respond ONLY with valid JSON matching this schema:
{
  "summary": "string",
  "risk_explanation": "string",
  "plain_english_reasoning": "string",
  "assets": [
    {"symbol": "string", "asset_type": "stock|crypto|option", "allocation_pct": number, "rationale": "string"}
  ]
}
Allocations should sum to roughly 100. Use simple tickers (e.g. VOO, AAPL, BTC).
Be conservative with options — only suggest if risk is high and explain clearly.
"""


def _mock_recommendation(
    risk: str, goals: str | None, horizon: str | None
) -> RecommendationResponse:
    """Deterministic fallback when OpenAI is unavailable."""
    if risk == "low":
        assets = [
            RecommendedAsset(
                symbol="VOO",
                asset_type="stock",
                allocation_pct=60.0,
                rationale="Broad US market ETF lowers single-stock risk.",
            ),
            RecommendedAsset(
                symbol="BND",
                asset_type="stock",
                allocation_pct=30.0,
                rationale="Bonds add stability for low risk tolerance.",
            ),
            RecommendedAsset(
                symbol="BTC",
                asset_type="crypto",
                allocation_pct=10.0,
                rationale="Small crypto slice for diversification (optional).",
            ),
        ]
        risk_exp = "Low risk: emphasis on ETFs and stability."
    elif risk == "high":
        assets = [
            RecommendedAsset(
                symbol="QQQ",
                asset_type="stock",
                allocation_pct=40.0,
                rationale="Growth tilt fits higher risk appetite.",
            ),
            RecommendedAsset(
                symbol="SOL",
                asset_type="crypto",
                allocation_pct=25.0,
                rationale="Higher volatility crypto allocation.",
            ),
            RecommendedAsset(
                symbol="AAPL",
                asset_type="stock",
                allocation_pct=35.0,
                rationale="Quality large-cap for core equity exposure.",
            ),
        ]
        risk_exp = "Higher risk: more growth and crypto; expect larger swings."
    else:
        assets = [
            RecommendedAsset(
                symbol="SPY",
                asset_type="stock",
                allocation_pct=50.0,
                rationale="Core US equity exposure.",
            ),
            RecommendedAsset(
                symbol="ETH",
                asset_type="crypto",
                allocation_pct=15.0,
                rationale="Moderate crypto allocation.",
            ),
            RecommendedAsset(
                symbol="MSFT",
                asset_type="stock",
                allocation_pct=35.0,
                rationale="Balanced large-cap tech.",
            ),
        ]
        risk_exp = "Medium risk: balanced stocks with modest crypto."

    g = goals or "general wealth building"
    h = horizon or "unspecified"
    return RecommendationResponse(
        summary=f"Portfolio tilt for {risk} risk, goals: {g[:80]}..., horizon: {h}.",
        risk_explanation=risk_exp,
        assets=assets,
        plain_english_reasoning=(
            "We diversify across asset types so one bad day in a single name "
            "doesn't define your outcome. Start small, invest regularly, and "
            "revisit as your situation changes."
        ),
    )


def _parse_json_loose(text: str) -> dict[str, Any]:
    text = text.strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        text = m.group(0)
    return json.loads(text)


async def generate_recommendation(
    risk_tolerance: str,
    investment_goals: str | None,
    time_horizon: str | None,
    market_context: str,
) -> RecommendationResponse:
    settings = get_settings()
    if not settings.openai_api_key:
        return _mock_recommendation(
            risk_tolerance, investment_goals, time_horizon
        )

    client = OpenAI(api_key=settings.openai_api_key)
    user_prompt = f"""Given a user with risk tolerance: {risk_tolerance}.
Investment goals: {investment_goals or 'not specified'}.
Time horizon: {time_horizon or 'not specified'}.
Market context (snippets): {market_context}
Suggest a diversified portfolio and explain simply."""

    try:
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.4,
        )
        raw = resp.choices[0].message.content or "{}"
        data = _parse_json_loose(raw)
        assets = [
            RecommendedAsset(**a) for a in data.get("assets", [])
        ]
        return RecommendationResponse(
            summary=data.get("summary", ""),
            risk_explanation=data.get("risk_explanation", ""),
            plain_english_reasoning=data.get(
                "plain_english_reasoning", ""
            ),
            assets=assets or _mock_recommendation(
                risk_tolerance, investment_goals, time_horizon
            ).assets,
        )
    except Exception:
        return _mock_recommendation(
            risk_tolerance, investment_goals, time_horizon
        )
