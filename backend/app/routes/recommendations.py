from fastapi import APIRouter

from app.deps import CurrentUser, DbSession
from app.ai.recommendation_engine import generate_recommendation
from app.schemas.recommendation import RecommendationRequest, RecommendationResponse
from app.services import market_data

router = APIRouter(tags=["recommendations"])


@router.post("/generate-recommendation", response_model=RecommendationResponse)
async def generate_recommendation_endpoint(
    body: RecommendationRequest,
    db: DbSession,
    user: CurrentUser,
) -> RecommendationResponse:
    risk = body.risk_tolerance or user.risk_tolerance or "medium"
    goals = body.investment_goals or user.investment_goals
    horizon = body.time_horizon or user.time_horizon

    # Lightweight "market context" from a few symbols
    snippets = []
    for sym in ("SPY", "BTC", "ETH"):
        if sym == "SPY":
            p = await market_data.fetch_stock_price(sym)
        else:
            p = await market_data.fetch_crypto_price_usd(sym)
        if p:
            snippets.append(f"{sym} ~ ${p:.2f}")
    market_context = "; ".join(snippets) if snippets else "No live data; using mocks."

    return await generate_recommendation(risk, goals, horizon, market_context)
