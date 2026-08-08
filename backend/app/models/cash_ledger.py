"""Audit trail for every Portfolio.cash_balance change — one row per
mutation, written alongside the balance update by
app/services/cash_ledger.py's apply_cash_delta, the only place
cash_balance is allowed to change. Lets cash_balance be reconstructed
(and sanity-checked) as starting balance + sum(entries) instead of being
trusted as an opaque running total."""

from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class CashLedgerEntry(Base):
    __tablename__ = "cash_ledger_entries"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    portfolio_id: Mapped[int] = mapped_column(
        ForeignKey("portfolios.id", ondelete="CASCADE"), index=True
    )
    # trade | deposit | withdrawal today; fee/dividend are reserved
    # vocabulary for later — no code path produces them yet.
    entry_type: Mapped[str] = mapped_column(String(16), index=True)
    # Positive = credit (cash in), negative = debit (cash out).
    amount: Mapped[float] = mapped_column(Float)
    # portfolio.cash_balance immediately after this entry, so the
    # balance at any point in history is a direct read, not a
    # recomputation over every prior entry.
    balance_after: Mapped[float] = mapped_column(Float)
    # Set only when entry_type == "trade"; null for deposits/withdrawals.
    trade_id: Mapped[int | None] = mapped_column(
        ForeignKey("trades.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )

    portfolio = relationship("Portfolio", back_populates="cash_ledger_entries")
    trade = relationship("Trade")
