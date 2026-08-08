from pydantic import BaseModel, Field


class QuestionOptionOut(BaseModel):
    value: str
    label: str


class QuestionOut(BaseModel):
    id: str
    section: str
    prompt: str
    options: list[QuestionOptionOut]


class AnswerIn(BaseModel):
    question_id: str
    answer_value: str


class OnboardingSubmission(BaseModel):
    """Only answer_value is accepted per question — points are always
    looked up server-side from app/services/risk_questionnaire.py, never
    trusted from the client."""

    answers: list[AnswerIn] = Field(..., min_length=1)
    # Optional free-text elaboration, written to User.investment_goals.
    # No longer the primary goals input (goal_primary/goal_flexibility/
    # goal_priority_growth above cover that) — nullable, unlike the old
    # required investment_goals field.
    elaboration: str | None = Field(default=None, max_length=2000)


class OnboardingStateOut(BaseModel):
    completed: bool
    answers: dict[str, str]
    elaboration: str | None
    risk_tolerance: str | None
