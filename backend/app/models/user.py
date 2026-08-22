"""User profile linked to Firebase UID."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, String, Text
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

    # Display timezone for times shown in the app: either an IANA name
    # (e.g. "America/Chicago") or the sentinel "device" meaning "render in
    # whatever timezone the client's device is in" (the default — see
    # app/routes/settings.py's set_timezone). Distinct from the trading
    # engine's own market clock (agent/market_hours.py's MARKET_TZ, always
    # America/New_York regardless of this field).
    timezone: Mapped[str] = mapped_column(String(64), default="device")

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Cash/holdings live on Portfolio now (see app.models.portfolio) — this
    # is a one-per-user account for trades, snapshots, and agent decisions.
    portfolio = relationship(
        "Portfolio", back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
