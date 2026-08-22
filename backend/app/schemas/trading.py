from datetime import datetime
from datetime import timezone as dt_timezone
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

# agent/market_hours.py's MARKET_TZ — duplicated rather than imported to
# avoid a schemas -> agent import (schemas is meant to stay a leaf module).
_MARKET_TZ = ZoneInfo("America/New_York")


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

    @field_serializer("timestamp")
    def _serialize_timestamp(self, dt: datetime) -> str:
        # Stored naive-UTC (Trade.timestamp's datetime.utcnow() default) —
        # make that explicit on the wire so clients don't misread it as
        # local time. Same pattern as SnapshotPointOut/HoldingPricePointOut
        # in schemas/dashboard.py.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=dt_timezone.utc)
        return dt.isoformat()

    @field_serializer("scheduled_execution_time", when_used="unless-none")
    def _serialize_scheduled(self, dt: datetime) -> str:
        # Unlike `timestamp` above, this is stored naive-*ET*:
        # agent/market_hours.py's next_market_open() returns a tz-aware
        # America/New_York datetime, and its tzinfo is silently dropped on
        # save (see app.services.portfolio_service.queue_pending_trade).
        # Attaching UTC here the way `timestamp` does would misrepresent
        # it by the ET/UTC offset (4-5 hours).
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_MARKET_TZ)
        return dt.isoformat()
