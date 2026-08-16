from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


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


class PriceOut(BaseModel):
    symbol: str
    asset_type: str
    price: float


class TradeOut(BaseModel):
    """A single row from Trade, as returned by the trade history endpoint.

    price is nullable here even though Trade.price is NOT NULL at the DB
    level: a status="pending" (queued off-hours) row stores a 0.0
    placeholder internally (see portfolio_service.queue_pending_trade's
    docstring for why), which this always nulls out below — callers
    (the Flutter trade history list, stock detail's recent trades) must
    never be handed a fake price for a trade that hasn't actually filled.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    symbol: str
    asset_type: str
    side: str
    quantity: float
    price: float | None
    status: str
    simulated: bool
    source: str
    order_id: str | None = None
    scheduled_execution_time: datetime | None = None

    @model_validator(mode="after")
    def _null_price_for_pending(self) -> "TradeOut":
        if self.status == "pending":
            self.price = None
        return self
