"""Append-only history of obvious/non-obvious classification runs
(agent/classification.py) — see docs/DISCOVERY_DESIGN.md §3.

One row per ticker per classification run (the fixed 6 TARGET_PORTFOLIOS
stock/ETF tickers, plus every distinct ticker ever discovered via
agent/discovery.py) — a NEW table rather than extending
DiscoveredCandidate, because the grain is different: DiscoveredCandidate
is one row per discovery EVENT (a specific article surfacing a specific
ticker, with source-article traceability), while this is one row per
ticker per classification RUN. The fixed 6 tickers have no
DiscoveredCandidate row at all (they were never "discovered"), so
attaching classification data to that table wouldn't even be structurally
possible for them. Deliberately never updated/overwritten — always a new
INSERT per run — so classification drift is visible over time, not
silently lost."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TickerClassification(Base):
    __tablename__ = "ticker_classifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    classified_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    is_obvious: Mapped[bool] = mapped_column(Boolean)

    gate_a_passed: Mapped[bool] = mapped_column(Boolean)
    gate_a_detail: Mapped[str] = mapped_column(String(255))
    gate_b_passed: Mapped[bool] = mapped_column(Boolean)
    gate_b_detail: Mapped[str] = mapped_column(String(255))
    gate_c_passed: Mapped[bool] = mapped_column(Boolean)
    gate_c_detail: Mapped[str] = mapped_column(String(255))

    # Null when Gate A/C couldn't compute (insufficient price history).
    volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    voo_volatility: Mapped[float | None] = mapped_column(Float, nullable=True)
    vol_ratio: Mapped[float | None] = mapped_column(Float, nullable=True)
