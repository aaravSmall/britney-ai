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
    portfolio_id: int
    cash_balance: float
    total_portfolio_value: float
    day_change_pct: float | None
    holdings: list[HoldingOut]


class PortfolioListItem(BaseModel):
    """One entry in the portfolio switcher: the caller's own portfolio,
    plus the agent's three risk-tier model portfolios."""

    id: int
    label: str
    owner_type: str  # user | agent
    risk_tolerance: str | None


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


class HoldingPricePointOut(BaseModel):
    """One symbol's price/quantity/value at a single PortfolioSnapshot
    timestamp, as returned by GET /dashboard/performance/{symbol} and
    GET /portfolios/{id}/performance/{symbol} — the same PortfolioSnapshot
    rows behind SnapshotPointOut/the portfolio-level chart, just reading
    one entry out of each row's per-holding `holdings` breakdown instead
    of the aggregate total_value. Built from a plain dict (see
    portfolio_snapshot_service.get_symbol_price_history), not an ORM row,
    so no from_attributes config here."""

    timestamp: datetime
    price: float
    quantity: float
    value: float

    @field_serializer("timestamp")
    def _serialize_timestamp(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
