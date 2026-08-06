from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer


class HoldingOut(BaseModel):
    symbol: str
    asset_type: str
    quantity: float
    avg_cost: float
    last_price: float | None
    market_value: float | None = None


class DashboardResponse(BaseModel):
    cash_balance: float
    total_portfolio_value: float
    day_change_pct: float | None
    holdings: list[HoldingOut]


class SnapshotPointOut(BaseModel):
    """One PortfolioSnapshot row, as returned by GET /dashboard/performance."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    total_value: float
    cash: float

    @field_serializer("timestamp")
    def _serialize_timestamp(self, dt: datetime) -> str:
        # Stored naive-UTC; make that explicit on the wire so clients don't
        # misread the timestamp as local time.
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
