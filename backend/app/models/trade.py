"""Executed (or simulated) fills — one row per trade, for real trade history."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    asset_type: Mapped[str] = mapped_column(
        String(16), default="stock"
    )  # stock | crypto | option
    side: Mapped[str] = mapped_column(String(4))  # buy | sell
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(16), default="filled")
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(
        String(16), default="user"
    )  # user | agent | auto_invest | rebalance
    order_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    # Set only for a queued off-hours stock trade (status="pending") — the
    # next NYSE 9:30am ET open it's due to fill at (agent/market_hours.py's
    # next_market_open()). Null for every immediately-filled trade (the
    # overwhelming majority: all crypto, all during-hours stock, every user
    # trade). See app.services.portfolio_service.queue_pending_trade/
    # fill_pending_trade.
    scheduled_execution_time: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )

    portfolio = relationship("Portfolio", back_populates="trades")
    agent_decision = relationship(
        "AgentDecision", back_populates="trade", uselist=False
    )
