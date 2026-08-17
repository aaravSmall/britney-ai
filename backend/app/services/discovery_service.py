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
