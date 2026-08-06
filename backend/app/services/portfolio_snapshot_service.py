"""CRUD helpers for PortfolioSnapshot — daily portfolio value history."""

from datetime import datetime

from sqlalchemy.orm import Session

from app.models import PortfolioSnapshot


def create_snapshot(
    db: Session,
    *,
    portfolio_id: int,
    total_value: float,
    cash: float,
    holdings: list[dict],
    timestamp: datetime | None = None,
) -> PortfolioSnapshot:
    row = PortfolioSnapshot(
        portfolio_id=portfolio_id,
        timestamp=timestamp or datetime.utcnow(),
        total_value=total_value,
        cash=cash,
        holdings=holdings,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_snapshots(
    db: Session,
    portfolio_id: int,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[PortfolioSnapshot]:
    query = db.query(PortfolioSnapshot).filter(
        PortfolioSnapshot.portfolio_id == portfolio_id
    )
    if start is not None:
        query = query.filter(PortfolioSnapshot.timestamp >= start)
    if end is not None:
        query = query.filter(PortfolioSnapshot.timestamp <= end)
    return query.order_by(PortfolioSnapshot.timestamp.asc()).all()
