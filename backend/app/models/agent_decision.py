"""Audit log for the autonomous trading agent: one row per decision it makes,
whether or not that decision resulted in a trade."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AgentDecision(Base):
    __tablename__ = "agent_decisions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    tickers: Mapped[list] = mapped_column(JSON, default=list)
    news_source: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )  # e.g. "finnhub"
    news_article_ids: Mapped[list] = mapped_column(JSON, default=list)
    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[float] = mapped_column(Float)
    decision: Mapped[str] = mapped_column(String(8))  # buy | sell | hold
    reasoning: Mapped[str] = mapped_column(Text)
    trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("trades.id", ondelete="SET NULL"), nullable=True
    )

    portfolio = relationship("Portfolio", back_populates="agent_decisions")
    trade = relationship("Trade", back_populates="agent_decision")
