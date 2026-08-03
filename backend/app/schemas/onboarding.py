from pydantic import BaseModel, Field


class OnboardingUpdate(BaseModel):
    risk_tolerance: str = Field(..., pattern="^(low|medium|high)$")
    investment_goals: str = Field(..., min_length=1, max_length=2000)
    time_horizon: str = Field(
        ..., description="e.g. under_1_year, 1_5_years, 5_plus_years"
    )
