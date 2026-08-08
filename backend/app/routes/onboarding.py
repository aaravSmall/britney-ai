"""Risk questionnaire onboarding.

GET /onboarding/questions serves the canonical question bank (public,
static content, no user data — same no-auth posture as
app/routes/stocks.py's market data). GET /onboarding returns the
current user's saved answers/elaboration/computed tier, for the wizard
to prefill on reopen. POST /onboarding submits a full set of answers,
computes the low/medium/high bucket server-side (see
app/services/risk_questionnaire.py), and writes it to
User.risk_tolerance — the exact same 3-value vocabulary
ai/recommendation_engine.py, agent/decision_loop.py, and
ai/chat_engine.py already consume; none of them change.

User.time_horizon is intentionally left alone here (untouched, stays
whatever it was — null for any new user) rather than synthesized from
the new time_horizon questions: those three questions get blended into
one overall score alongside loss-tolerance/experience/goals, so there's
no longer a single canonical "time horizon" value to store there. The
only consumer that reads it (ai/chat_engine.py, display-only) already
handles a null gracefully ("Horizon: not set").
"""

from fastapi import APIRouter, HTTPException

from app.deps import CurrentUser, DbSession
from app.models import RiskQuestionnaireResponse
from app.schemas.onboarding import (
    OnboardingStateOut,
    OnboardingSubmission,
    QuestionOptionOut,
    QuestionOut,
)
from app.services.risk_questionnaire import QUESTIONS, QUESTIONS_BY_ID, bucket_for_score

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.get("/questions", response_model=list[QuestionOut])
def get_questions() -> list[QuestionOut]:
    return [
        QuestionOut(
            id=q.id,
            section=q.section,
            prompt=q.prompt,
            options=[QuestionOptionOut(value=o.value, label=o.label) for o in q.options],
        )
        for q in QUESTIONS
    ]


@router.get("", response_model=OnboardingStateOut)
def get_onboarding_state(db: DbSession, user: CurrentUser) -> OnboardingStateOut:
    responses = (
        db.query(RiskQuestionnaireResponse)
        .filter(RiskQuestionnaireResponse.user_id == user.id)
        .all()
    )
    return OnboardingStateOut(
        completed=bool(responses),
        answers={r.question_id: r.answer_value for r in responses},
        elaboration=user.investment_goals,
        risk_tolerance=user.risk_tolerance,
    )


@router.post("", response_model=OnboardingStateOut)
def submit_onboarding(
    body: OnboardingSubmission,
    db: DbSession,
    user: CurrentUser,
) -> OnboardingStateOut:
    submitted_ids = [a.question_id for a in body.answers]
    submitted_id_set = set(submitted_ids)
    expected_ids = set(QUESTIONS_BY_ID.keys())

    if len(submitted_ids) != len(submitted_id_set):
        raise HTTPException(status_code=422, detail="duplicate question_id in submission")
    if submitted_id_set != expected_ids:
        missing = expected_ids - submitted_id_set
        extra = submitted_id_set - expected_ids
        parts = []
        if missing:
            parts.append(f"missing: {', '.join(sorted(missing))}")
        if extra:
            parts.append(f"unknown: {', '.join(sorted(extra))}")
        raise HTTPException(status_code=422, detail="; ".join(parts))

    rows: list[RiskQuestionnaireResponse] = []
    total_points = 0
    for answer in body.answers:
        question = QUESTIONS_BY_ID[answer.question_id]
        points = question.points_by_value.get(answer.answer_value)
        if points is None:
            raise HTTPException(
                status_code=422,
                detail=f"{answer.question_id}: invalid answer_value {answer.answer_value!r}",
            )
        total_points += points
        rows.append(
            RiskQuestionnaireResponse(
                user_id=user.id,
                question_id=answer.question_id,
                answer_value=answer.answer_value,
                points=points,
            )
        )

    bucket = bucket_for_score(total_points)

    db.query(RiskQuestionnaireResponse).filter(
        RiskQuestionnaireResponse.user_id == user.id
    ).delete()
    for row in rows:
        db.add(row)
    user.risk_tolerance = bucket
    user.investment_goals = body.elaboration
    db.add(user)
    db.commit()

    return OnboardingStateOut(
        completed=True,
        answers={a.question_id: a.answer_value for a in body.answers},
        elaboration=user.investment_goals,
        risk_tolerance=user.risk_tolerance,
    )
