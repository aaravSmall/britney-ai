"""DB-backed persistence and ticker-level dedup for the general-news
discovery pipeline (agent/discovery.py, agent/run_discovery.py). Kept
separate from discovery.py itself so that module stays standalone/
DB-free (same reasoning as agent_decision_service.py existing alongside
decision_loop.py, rather than decision_loop.py owning DB writes
directly)."""

from __future__ import annotations

from datetime import datetime
from datetime import time as dtime

from sqlalchemy.orm import Session

from app.models import DiscoveredCandidate


def was_ticker_discovered_today(db: Session, ticker: str) -> bool:
    """Day-scoped ticker-level dedup: true if a validated candidate for
    `ticker` already exists with discovered_at on today's UTC date.

    This IS the ticker-level dedup mechanism, separate from
    agent/discovery.py's SeenArticleStore-based article-level dedup —
    the discovered_candidates table doubles as both this pipeline's
    output and its own source of truth for "was this ticker already
    found today," rather than a second cache table that could drift out
    of sync with what's actually persisted. Callers (agent/run_discovery.py)
    check this BEFORE running the (more expensive, network-calling)
    validation gates, so a same-day duplicate mention short-circuits
    before ever calling stock_data.quote() again.

    Deliberately date-scoped, not cycle-scoped: at an hourly poll
    cadence, the goal is "one validated candidate per ticker per day,"
    not "per 60-minute window" — see docs/DISCOVERY_DESIGN.md §6.
    """
    start_of_day = datetime.combine(datetime.utcnow().date(), dtime.min)
    return (
        db.query(DiscoveredCandidate)
        .filter(
            DiscoveredCandidate.ticker == ticker,
            DiscoveredCandidate.discovered_at >= start_of_day,
        )
        .first()
        is not None
    )


def distinct_discovered_tickers(db: Session) -> set[str]:
    """Every ticker that has ever cleared discovery's validation gates,
    regardless of which day — the candidate pool agent/classification.py
    classifies alongside the fixed 6 target tickers. Distinct because a
    ticker can have multiple discovered_candidates rows (discovered on
    different days) — classification is a per-ticker property, not a
    per-discovery-event one."""
    rows = db.query(DiscoveredCandidate.ticker).distinct().all()
    return {row[0] for row in rows}


def latest_confidence_by_ticker(db: Session) -> dict[str, float]:
    """Each discovered ticker's confidence from its MOST RECENT discovery
    row (not max/first) — used to rank non-obvious candidates within a
    tier's concurrency cap (agent/classification.py's route_non_obvious()),
    on the theory that the freshest signal is the most decision-relevant
    one, not necessarily the strongest one it ever had."""
    rows = (
        db.query(DiscoveredCandidate)
        .order_by(DiscoveredCandidate.ticker, DiscoveredCandidate.discovered_at.desc())
        .all()
    )
    result: dict[str, float] = {}
    for row in rows:
        if row.ticker not in result:  # first row per ticker in this order == most recent
            result[row.ticker] = row.confidence
    return result


def create_discovered_candidate(
    db: Session,
    *,
    ticker: str,
    company_name: str,
    source_article_id: str,
    source_headline: str,
    confidence: float,
) -> DiscoveredCandidate:
    candidate = DiscoveredCandidate(
        ticker=ticker,
        company_name=company_name,
        source_article_id=source_article_id,
        source_headline=source_headline,
        confidence=confidence,
    )
    db.add(candidate)
    db.commit()
    db.refresh(candidate)
    return candidate
