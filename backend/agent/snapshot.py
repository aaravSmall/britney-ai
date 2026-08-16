"""
Portfolio snapshot capture for the britney.ai trading agent.

Writes one PortfolioSnapshot row per call — total_value, cash, and a
per-holding price/value breakdown — reusing the exact same
app.services.market_data price-fetching path (Yahoo Finance for stocks,
CoinGecko for crypto, with the same mock fallbacks) that decision_loop.py
already uses to price trades. Called once per portfolio at the end of
every agent.run_agent.py poll cycle, regardless of whether that cycle's
decision_loop run made a trade, so snapshot history tracks the same
market-hours-aware cadence as decisions/trades rather than a schedule of
its own.

Self-contained like news_ingestion.py/sentiment.py's public entry points:
takes a portfolio_id and manages its own DB session, so run_agent.py can
call it independently of decision_loop.py's own session lifecycle.

    cd backend && python -m agent.snapshot <portfolio_id>
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Portfolio, PortfolioSnapshot
from app.services import market_data
from app.services.portfolio_snapshot_service import create_snapshot

logger = logging.getLogger(__name__)

# Guards against writing a duplicate snapshot if run_agent.py restarts
# mid-cycle (e.g. process killed right after a snapshot commits but before
# the next sleep). Set comfortably under the poll interval
# (POLL_MINUTES=3 in run_agent.py, flat 24/7 as of the cadence change) so
# a legitimate next-cycle snapshot is never mistaken for a duplicate of
# the previous one — same ~2/3 ratio as the original 10-under-15 pairing
# this replaced; that 10 stopped being "comfortably under" once the poll
# interval dropped to 3 without this changing too, and was observed
# silently swallowing most real snapshots as a result (every cycle inside
# the same 10-minute window after the first, not just genuine restarts).
DEDUPE_WINDOW_MINUTES = 2


def _recent_snapshot_exists(db: Session, portfolio_id: int, window_minutes: int) -> bool:
    cutoff = datetime.utcnow() - timedelta(minutes=window_minutes)
    return (
        db.query(PortfolioSnapshot)
        .filter(
            PortfolioSnapshot.portfolio_id == portfolio_id,
            PortfolioSnapshot.timestamp >= cutoff,
        )
        .first()
        is not None
    )


async def _price_holdings(
    portfolio: Portfolio,
) -> tuple[float, list[dict[str, float | str]]]:
    """Returns (total_holdings_value, breakdown) where breakdown has one
    dict per holding: symbol, quantity, price, value. Falls back to the
    holding's cached last_price/avg_cost if a live quote can't be fetched
    (mirrors decision_loop._execute's degradation — a stale price is much
    better than dropping the holding out of the snapshot entirely)."""
    total = 0.0
    breakdown: list[dict[str, float | str]] = []
    for h in portfolio.holdings:
        price = await market_data.get_price_for_holding(h.symbol, h.asset_type)
        if price is None:
            price = h.last_price or h.avg_cost
        value = h.quantity * price
        total += value
        breakdown.append(
            {
                "symbol": h.symbol,
                "quantity": h.quantity,
                "price": round(price, 6),
                "value": round(value, 2),
            }
        )
    return total, breakdown


async def snapshot_capture(
    portfolio_id: int,
    *,
    dedupe_window_minutes: int = DEDUPE_WINDOW_MINUTES,
) -> PortfolioSnapshot | None:
    """Compute and write one PortfolioSnapshot row for `portfolio_id`.

    Never raises for expected failure modes (missing portfolio, price
    fetch/DB error) — logs loudly instead and returns None, so a bad
    snapshot for one portfolio can't take down the caller's loop over the
    rest. Returns None for a skipped (recent duplicate exists) snapshot
    too; the log line is what distinguishes skip vs. failure vs. success.
    """
    db = SessionLocal()
    try:
        portfolio = db.query(Portfolio).filter(Portfolio.id == portfolio_id).one_or_none()
        if portfolio is None:
            logger.error(
                "snapshot failed: portfolio=%s does not exist.", portfolio_id
            )
            return None

        if _recent_snapshot_exists(db, portfolio.id, dedupe_window_minutes):
            logger.info(
                "snapshot skipped (duplicate): portfolio=%s already has a snapshot "
                "within the last %d min.",
                portfolio.id, dedupe_window_minutes,
            )
            return None

        try:
            holdings_value, breakdown = await _price_holdings(portfolio)
            total_value = portfolio.cash_balance + holdings_value
            row = create_snapshot(
                db,
                portfolio_id=portfolio.id,
                total_value=round(total_value, 2),
                cash=portfolio.cash_balance,
                holdings=breakdown,
            )
        except Exception as exc:
            logger.error(
                "snapshot failed: portfolio=%s could not be captured (%s: %s).",
                portfolio.id, type(exc).__name__, exc,
            )
            return None

        logger.info(
            "snapshot captured: portfolio=%s total_value=$%.2f cash=$%.2f holdings=%d",
            row.portfolio_id, row.total_value, row.cash, len(breakdown),
        )
        return row
    finally:
        db.close()


async def _main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    if len(sys.argv) < 2:
        print("usage: python -m agent.snapshot <portfolio_id>")
        sys.exit(1)
    await snapshot_capture(int(sys.argv[1]))


if __name__ == "__main__":
    asyncio.run(_main())
