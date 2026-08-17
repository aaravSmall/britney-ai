"""Output of the general-news discovery pipeline (agent/discovery.py,
agent/run_discovery.py) — see docs/DISCOVERY_DESIGN.md. Each row is a
company mention that already passed all three hard validation gates
(real quote, name-matched, not already a fixed TARGET_PORTFOLIOS
ticker) — see agent/discovery.py's validate_candidate().

Output-only for now: nothing reads from this table yet. Sentiment
scoring, obvious/non-obvious classification, and rebalance-bucket
integration are staged in later prompts, deliberately out of scope for
the discovery pipeline this model backs.

This table doubles as this pipeline's own ticker-level, day-scoped dedup
source of truth (see app/services/discovery_service.py's
was_ticker_discovered_today()) rather than a separate cache — there's no
second piece of state that could drift out of sync with what's actually
been persisted."""

from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class DiscoveredCandidate(Base):
    __tablename__ = "discovered_candidates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(32), index=True)
    company_name: Mapped[str] = mapped_column(String(255))
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, index=True
    )
    # Traceability back to the general-news article that produced this
    # candidate — source_article_id matches agent/discovery.py's
    # NewsArticle.article_id ("finnhub-general:<id>"), not any of
    # news_ingestion.py's per-ticker article ids.
    source_article_id: Mapped[str] = mapped_column(String(128))
    source_headline: Mapped[str] = mapped_column(String(512))
    confidence: Mapped[float] = mapped_column(Float)
