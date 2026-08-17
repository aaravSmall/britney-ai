"""Current debounce state for obvious/non-obvious classification — see
agent/classification.py's update_streaks() and docs on
agent_config.CLASSIFICATION_DEBOUNCE_DAYS.

Deliberately a SEPARATE table from the append-only TickerClassification
history, not an extra column there: TickerClassification is one row per
ticker per RUN (a permanent audit log, never overwritten — see that
model's docstring), while this is one row per ticker, upserted every
run — "what is today's raw signal, how many consecutive days has it held,
and what's the current debounced effective status" is CURRENT STATE, not
history. Mixing the two grains into one table would mean either
duplicating this state onto every historical row (redundant, and
ambiguous about which row is authoritative) or losing the append-only
history property.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TickerStreakState(Base):
    __tablename__ = "ticker_streak_state"

    ticker: Mapped[str] = mapped_column(String(32), primary_key=True)
    # Most recent day's raw (un-debounced) is_obvious result.
    raw_status: Mapped[bool] = mapped_column(Boolean)
    # How many consecutive days raw_status has held its current value.
    consecutive_days: Mapped[int] = mapped_column(Integer)
    # The debounced status actually used for allocation/sell decisions —
    # only changes once consecutive_days reaches
    # agent_config.CLASSIFICATION_DEBOUNCE_DAYS AND raw_status disagrees
    # with it.
    effective_status: Mapped[bool] = mapped_column(Boolean)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )
