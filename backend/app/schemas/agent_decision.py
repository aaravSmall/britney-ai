"""Response models for the AI trade-rationale "More info" view — a
decision's reasoning/confidence/sentiment plus the full news citations
and per-article scores behind it (app/models/agent_decision.py)."""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, field_serializer


class NewsArticleCitationOut(BaseModel):
    article_id: str
    ticker: str
    headline: str
    source: str
    url: str
    published_at: str  # already an ISO string in the stored JSON
    sentiment: str  # bullish | bearish | neutral
    confidence: float
    reasoning: str


class AgentDecisionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    timestamp: datetime
    tickers: list[str]
    decision: str  # buy | sell | hold
    reasoning: str
    confidence: float
    sentiment_score: float | None
    news_source: str | None
    articles: list[NewsArticleCitationOut]
    trade_id: int | None

    @field_serializer("timestamp")
    def _serialize_timestamp(self, dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
