"""Request body for POST /portfolios/{id}/deposit and /withdraw."""

from pydantic import BaseModel, Field


class CashAmountRequest(BaseModel):
    amount: float = Field(..., gt=0)
    note: str | None = None
