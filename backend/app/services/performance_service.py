"""Grounds chat's answers about past portfolio performance in real data,
instead of letting the model guess at what happened on a given date.

Built for app.ai.chat_engine's get_portfolio_activity tool: a user (or
the agent's own portfolios) asking "why did I lose money on <date>" needs
real trades, cash movements, and price history behind the answer, not a
plausible-sounding fabrication. Reuses the same data every other part of
the app already trusts — Trade/CashLedgerEntry/PortfolioSnapshot rows and
app.services.stock_data.history()'s daily candles (the same source
agent/stop_loss.py checks) — rather than inventing a parallel path.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import CashLedgerEntry, Portfolio, PortfolioSnapshot, Trade
from app.services import stock_data

# Mirrors agent/stop_loss.py's _yahoo_symbol mapping — Yahoo's chart
# endpoint needs crypto tickers in "<SYMBOL>-USD" pair form.
def _yahoo_symbol(symbol: str, asset_type: str) -> str:
    return f"{symbol}-USD" if asset_type == "crypto" else symbol


def _parse_date(value: str | None, default: datetime) -> datetime:
    if not value:
        return default
    return datetime.fromisoformat(value)


async def portfolio_activity_report(
    db: Session,
    portfolio: Portfolio,
    start_date: str | None = None,
    end_date: str | None = None,
) -> str:
    """Text report of what actually happened to `portfolio` between
    start_date and end_date (ISO "YYYY-MM-DD", both optional — defaults
    to the trailing 14 days ending now): trades filled, cash deposits/
    withdrawals, the biggest portfolio value swings on record (if this
    portfolio has snapshot history — most user portfolios don't yet, see
    PortfolioSnapshot's docstring), and for each currently-held symbol,
    the daily price moves over the window from the same Yahoo daily-
    candle source agent/stop_loss.py already uses — so a pure
    mark-to-market swing with no trade behind it is still explainable,
    not just trade-driven ones."""
    end = _parse_date(end_date, datetime.utcnow())
    start = _parse_date(start_date, end - timedelta(days=14))

    lines: list[str] = [f"Activity report for {start.date()} to {end.date()}:"]

    trades = (
        db.query(Trade)
        .filter(Trade.portfolio_id == portfolio.id, Trade.timestamp.between(start, end))
        .order_by(Trade.timestamp)
        .all()
    )
    if trades:
        lines.append("\nTrades filled in this window:")
        for t in trades:
            lines.append(
                f"- {t.timestamp:%Y-%m-%d %H:%M} {t.side.upper()} {t.quantity} {t.symbol} "
                f"@ ${t.price:,.2f} (status={t.status}, source={t.source})"
            )
    else:
        lines.append("\nNo trades filled in this window.")

    non_trade_entries = (
        db.query(CashLedgerEntry)
        .filter(
            CashLedgerEntry.portfolio_id == portfolio.id,
            CashLedgerEntry.entry_type != "trade",
            CashLedgerEntry.created_at.between(start, end),
        )
        .order_by(CashLedgerEntry.created_at)
        .all()
    )
    if non_trade_entries:
        lines.append("\nDeposits/withdrawals in this window:")
        for e in non_trade_entries:
            lines.append(f"- {e.created_at:%Y-%m-%d} {e.entry_type} ${e.amount:,.2f}")

    snapshots = (
        db.query(PortfolioSnapshot)
        .filter(
            PortfolioSnapshot.portfolio_id == portfolio.id,
            PortfolioSnapshot.timestamp.between(start, end),
        )
        .order_by(PortfolioSnapshot.timestamp)
        .all()
    )
    if snapshots:
        first, last = snapshots[0], snapshots[-1]
        lines.append(
            f"\nRecorded total value: ${first.total_value:,.2f} on "
            f"{first.timestamp:%Y-%m-%d %H:%M} -> ${last.total_value:,.2f} on "
            f"{last.timestamp:%Y-%m-%d %H:%M} ({last.total_value - first.total_value:+,.2f})."
        )
        biggest_drop = None
        prev = None
        for snap in snapshots:
            if prev is not None:
                delta = snap.total_value - prev.total_value
                if delta < 0 and (biggest_drop is None or delta < biggest_drop[0]):
                    biggest_drop = (delta, prev, snap)
            prev = snap
        if biggest_drop:
            delta, before, after = biggest_drop
            lines.append(
                f"Biggest single recorded drop: {delta:+,.2f}, between "
                f"{before.timestamp:%Y-%m-%d %H:%M} (${before.total_value:,.2f}) and "
                f"{after.timestamp:%Y-%m-%d %H:%M} (${after.total_value:,.2f}). Check the "
                "trades above and the per-symbol price moves below for cause — if there's no "
                "trade at that time, it's a pure market price move, not an error."
            )
    else:
        lines.append(
            "\nNo portfolio value history recorded for this window (this account doesn't have "
            "snapshot tracking turned on yet, so only trades/deposits above are known)."
        )

    if portfolio.holdings:
        lines.append("\nCurrently-held symbols, price move over this window:")
        for h in portfolio.holdings:
            candles = await stock_data.history(_yahoo_symbol(h.symbol, h.asset_type), "30D")
            in_window = [
                c for c in candles if start <= datetime.fromisoformat(c["timestamp"]).replace(tzinfo=None) <= end
            ]
            if not in_window:
                lines.append(f"- {h.symbol}: no daily price history available for this window.")
                continue
            open_price = in_window[0]["open"]
            close_price = in_window[-1]["close"]
            pct = (close_price - open_price) / open_price * 100 if open_price else 0.0
            worst = min(in_window, key=lambda c: (c["close"] - c["open"]) / c["open"] if c["open"] else 0.0)
            lines.append(
                f"- {h.symbol}: ${open_price:,.2f} -> ${close_price:,.2f} ({pct:+.2f}%) over the "
                f"window; worst single day {datetime.fromisoformat(worst['timestamp']):%Y-%m-%d} "
                f"(${worst['open']:,.2f} -> ${worst['close']:,.2f})"
            )

    return "\n".join(lines)
