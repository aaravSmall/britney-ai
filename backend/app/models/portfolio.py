"""Mock-friendly holdings; ready for real broker sync later."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Portfolio(Base):
    """The account that trades, snapshots, and agent decisions attach to.
    Either owned by a real user (owner_type="user", user_id set — one
    portfolio per user) or by the autonomous agent (owner_type="agent",
    user_id NULL — one per risk tier, not backed by any User row)."""

    __tablename__ = "portfolios"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True, nullable=True
    )
    owner_type: Mapped[str] = mapped_column(String(8), default="user")  # user | agent
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    # Paper-trading cash balance; buys/sells debit and credit this directly.
    cash_balance: Mapped[float] = mapped_column(Float, default=10_000.0)

    # Only meaningful for owner_type="agent" portfolios (low|medium|high,
    # same vocabulary as User.risk_tolerance) — a real user's portfolio
    # instead reads risk tier off its User via the relationship below.
    risk_tolerance: Mapped[str | None] = mapped_column(String(32), nullable=True)

    user = relationship("User", back_populates="portfolio")
    holdings = relationship(
        "PortfolioHolding", back_populates="portfolio", cascade="all, delete-orphan"
    )
    trades = relationship(
        "Trade", back_populates="portfolio", cascade="all, delete-orphan"
    )
    snapshots = relationship(
        "PortfolioSnapshot", back_populates="portfolio", cascade="all, delete-orphan"
    )
    agent_decisions = relationship(
        "AgentDecision", back_populates="portfolio", cascade="all, delete-orphan"
    )


class PortfolioSnapshot(Base):
    """Daily portfolio value snapshot — real performance history, as
    opposed to the dashboard's current mocked random-walk chart."""

    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    total_value: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    holdings: Mapped[dict] = mapped_column(JSON, default=dict)  # ticker -> quantity

    portfolio = relationship("Portfolio", back_populates="snapshots")


class PortfolioHolding(Base):
    __tablename__ = "portfolio_holdings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_type: Mapped[str] = mapped_column(
        String(16), default="stock"
    )  # stock | crypto | option
    quantity: Mapped[float] = mapped_column(Float)
    avg_cost: Mapped[float] = mapped_column(Float)
    # Cached last price for dashboard (refreshed by market service)
    last_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    portfolio = relationship("Portfolio", back_populates="holdings")
