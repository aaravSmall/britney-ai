"""User profile linked to Firebase UID."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    firebase_uid: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    email: Mapped[str] = mapped_column(String(255), index=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Onboarding / preferences
    risk_tolerance: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )  # low | medium | high
    investment_goals: Mapped[str | None] = mapped_column(Text, nullable=True)
    time_horizon: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # e.g. short, medium, long

    auto_invest_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Paper-trading cash balance; buys/sells debit and credit this directly.
    cash_balance: Mapped[float] = mapped_column(Float, default=10_000.0)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    holdings = relationship(
        "PortfolioHolding", back_populates="user", cascade="all, delete-orphan"
    )
