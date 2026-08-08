"""Recurring auto-invest schedules — fixed $ amount into one ticker on a
daily/weekly/monthly cadence, executed by the standalone
backend/agent/run_auto_invest.py loop (app/services/auto_invest_service.py
holds the is_due/execute logic both it and the test suite call).

Unrelated to User.auto_invest_enabled (app/models/user.py), which only
gates simulated-vs-live execution on manually submitted trades — that
column and its behavior are untouched by this feature."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AutoInvestSchedule(Base):
    __tablename__ = "auto_invest_schedules"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    # Always the user's own portfolio (never an agent one) — resolved
    # server-side at creation via get_or_create_portfolio, never taken
    # from the client.
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    asset_type: Mapped[str] = mapped_column(String(16), default="stock")  # stock | crypto
    # Fixed dollar amount bought each execution — no allocation/percentage
    # logic, one ticker per schedule row.
    amount: Mapped[float] = mapped_column(Float)
    interval: Mapped[str] = mapped_column(String(16))  # daily | weekly | monthly
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_executed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user = relationship("User")
    portfolio = relationship("Portfolio")
