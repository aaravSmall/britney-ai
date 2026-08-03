from pydantic import BaseModel, Field


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
