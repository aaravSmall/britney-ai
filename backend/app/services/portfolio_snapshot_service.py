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


def get_symbol_price_history(
    db: Session,
    portfolio_id: int,
    symbol: str,
    start: datetime | None = None,
    end: datetime | None = None,
) -> list[dict]:
    """Per-symbol price/quantity/value series for a holding-detail chart —
    reads one entry out of each PortfolioSnapshot's `holdings` breakdown
    (written by agent/snapshot.py's _price_holdings) instead of building
    a new price history source. Works identically for stocks and crypto,
    since that breakdown is populated via market_data.get_price_for_holding
    for every holding regardless of asset_type. A snapshot from before the
    position was opened (or after it was fully closed) has no matching
    entry and is simply skipped, rather than padding with a zero point."""
    snapshots = get_snapshots(db, portfolio_id, start=start, end=end)
    key = symbol.upper()
    points: list[dict] = []
    for snap in snapshots:
        for h in snap.holdings or []:
            if str(h.get("symbol", "")).upper() == key:
                points.append(
                    {
                        "timestamp": snap.timestamp,
                        "price": h["price"],
                        "quantity": h["quantity"],
                        "value": h["value"],
                    }
                )
                break
    return points
