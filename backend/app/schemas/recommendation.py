from pydantic import BaseModel, Field


class RecommendedAsset(BaseModel):
    symbol: str
    asset_type: str = Field(..., description="stock | crypto | option")
    allocation_pct: float = Field(..., ge=0, le=100)
    rationale: str


class RecommendationRequest(BaseModel):
    """Optional overrides; server fills from DB if omitted."""

    risk_tolerance: str | None = None
    investment_goals: str | None = None
    time_horizon: str | None = None


class RecommendationResponse(BaseModel):
    summary: str
    risk_explanation: str
    assets: list[RecommendedAsset]
    plain_english_reasoning: str
    disclaimer: str = (
        "Educational only — not financial advice. Past performance does not "
        "guarantee future results."
    )
