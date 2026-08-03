from pydantic import BaseModel


class HoldingOut(BaseModel):
    symbol: str
    asset_type: str
    quantity: float
    avg_cost: float
    last_price: float | None
    market_value: float | None = None


class PerformancePoint(BaseModel):
    date: str
    value: float


class DashboardResponse(BaseModel):
    cash_balance: float
    total_portfolio_value: float
    day_change_pct: float | None
    holdings: list[HoldingOut]
    performance: list[PerformancePoint]
