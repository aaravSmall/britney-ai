"""Request/response shapes for /auto-invest/schedules (app/routes/auto_invest.py)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class CreateScheduleRequest(BaseModel):
    ticker: str
    asset_type: str = Field(default="stock", pattern="^(stock|crypto)$")
    amount: float = Field(..., gt=0)
    interval: str = Field(..., pattern="^(daily|weekly|monthly)$")


class UpdateScheduleRequest(BaseModel):
    """Ticker/asset_type/portfolio are immutable after creation — delete
    and recreate instead of changing what a schedule invests in."""

    enabled: bool | None = None
    amount: float | None = Field(default=None, gt=0)
    interval: str | None = Field(default=None, pattern="^(daily|weekly|monthly)$")


class ScheduleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ticker: str
    asset_type: str
    amount: float
    interval: str
    enabled: bool
    last_executed_at: datetime | None
    created_at: datetime
