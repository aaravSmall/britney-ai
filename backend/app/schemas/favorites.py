from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FavoriteAdded(BaseModel):
    """POST /favorites/{ticker} response — just confirms what was
    stored. GET /favorites is what enriches with live quote data; no
    need for both endpoints to do that round-trip."""

    model_config = ConfigDict(from_attributes=True)

    ticker: str
    created_at: datetime


class FavoriteOut(BaseModel):
    """One row for GET /favorites — enough to render a list item without
    a second round-trip per ticker. Quote fields are null (not omitted)
    if a live/mock quote couldn't be found for a favorited ticker, e.g.
    a delisted symbol — the favorite itself is still real."""

    ticker: str
    favorited_at: datetime
    company_name: str | None = None
    price: float | None = None
    change: float | None = None
    change_pct: float | None = None
