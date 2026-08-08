"""Single choke point for mutating Portfolio.cash_balance.

record_trade_fill (trade fills) and the deposit/withdraw endpoints
(routes/cash.py) are the only callers — both go through
apply_cash_delta rather than touching portfolio.cash_balance directly,
so every balance change is paired with a CashLedgerEntry audit row in
the same flush.
"""

from sqlalchemy.orm import Session

from app.models import CashLedgerEntry, Portfolio


def apply_cash_delta(
    db: Session,
    portfolio: Portfolio,
    amount: float,
    entry_type: str,
    *,
    trade_id: int | None = None,
    note: str | None = None,
) -> CashLedgerEntry:
    """Debits/credits portfolio.cash_balance by `amount` (positive =
    credit, negative = debit) and records a CashLedgerEntry with the
    resulting balance. Adds the entry to `db` but doesn't commit — the
    caller controls the transaction boundary (record_trade_fill commits
    once for the trade + its cash entry; the deposit/withdraw routes
    commit once per request)."""
    portfolio.cash_balance += amount
    entry = CashLedgerEntry(
        portfolio_id=portfolio.id,
        entry_type=entry_type,
        amount=amount,
        balance_after=portfolio.cash_balance,
        trade_id=trade_id,
        note=note,
    )
    db.add(entry)
    return entry
