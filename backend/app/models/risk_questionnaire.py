"""One row per answered question in a user's risk questionnaire —
stores the points at time of submission (not just the answer_value) so
a later change to app/services/risk_questionnaire.py's point mapping
can't silently rewrite the meaning of a historical score. Kept purely
as an audit/re-edit trail: User.risk_tolerance (the computed low/medium
/high bucket) is what every downstream consumer actually reads.

On each submission, routes/onboarding.py replaces (delete + insert)
this user's full row set rather than upserting individual questions —
simpler than tracking partial edits for a 12-question form that's
always submitted whole."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class RiskQuestionnaireResponse(Base):
    __tablename__ = "risk_questionnaire_responses"
    __table_args__ = (
        UniqueConstraint("user_id", "question_id", name="uq_risk_response_user_question"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[str] = mapped_column(String(64), index=True)
    answer_value: Mapped[str] = mapped_column(String(64))
    points: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user = relationship("User")
