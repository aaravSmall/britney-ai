from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TradeRequest(BaseModel):
    symbol: str
    asset_type: str = Field(default="stock", pattern="^(stock|crypto|option)$")
    side: str = Field(..., pattern="^(buy|sell)$")
    quantity: float = Field(..., gt=0)
    simulate_only: bool = True


class TradeResponse(BaseModel):
    status: str
    simulated: bool
    message: str
    order_id: str | None = None


class TradeOut(BaseModel):
    """A single row from Trade, as returned by the trade history endpoint."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    symbol: str
    asset_type: str
    side: str
    quantity: float
    price: float
    status: str
    simulated: bool
    source: str
    order_id: str | None = None
