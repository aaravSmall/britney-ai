"""Coverage for the risk-questionnaire onboarding rebuild:
GET /onboarding/questions (the canonical bank), GET /onboarding (saved
state, for wizard prefill), and POST /onboarding (scoring + bucketing
+ writing User.risk_tolerance).

Answer sets for the three representative buckets are built
programmatically from app.services.risk_questionnaire.QUESTIONS (every
question has options worth 1/2/3/4/5 points) rather than hardcoding
option values, so these tests stay correct if question wording changes
without changing the point structure.
"""

import pytest

from app.database import SessionLocal
from app.models import RiskQuestionnaireResponse, User
from app.services.risk_questionnaire import (
    HIGH_MIN,
    LOW_MAX,
    MAX_SCORE,
    MIN_SCORE,
    QUESTIONS,
    bucket_for_score,
)

USER_A_TOKEN = "onboarding-test-user-a"


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _answers_at_points(points: int) -> list[dict]:
    answers = []
    for q in QUESTIONS:
        opt = next(o for o in q.options if o.points == points)
        answers.append({"question_id": q.id, "answer_value": opt.value})
    return answers


def _get_user(token: str) -> User:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.firebase_uid == f"dev-{token}").one()
    finally:
        db.close()


# --- question bank ---------------------------------------------------


def test_get_questions_returns_full_bank(client):
    resp = client.get("/onboarding/questions")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 12
    for q in body:
        assert set(q.keys()) == {"id", "section", "prompt", "options"}
        assert len(q["options"]) == 5
        for opt in q["options"]:
            # points must never leak to the client — schema only exposes
            # value/label.
            assert set(opt.keys()) == {"value", "label"}
    sections = {q["section"] for q in body}
    assert sections == {"time_horizon", "loss_tolerance", "experience", "goals"}


# --- state before submission ------------------------------------------


def test_get_state_before_submission_is_empty(client):
    token = "onboarding-test-fresh-user"
    resp = client.get("/onboarding", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "completed": False,
        "answers": {},
        "elaboration": None,
        "risk_tolerance": None,
    }


# --- scoring / bucketing ------------------------------------------


@pytest.mark.parametrize(
    "points,expected_bucket",
    [(1, "low"), (3, "medium"), (5, "high")],
)
def test_submission_computes_expected_bucket(client, points, expected_bucket):
    token = f"onboarding-test-bucket-{points}"
    answers = _answers_at_points(points)
    resp = client.post(
        "/onboarding",
        json={"answers": answers, "elaboration": "Testing"},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_tolerance"] == expected_bucket
    assert body["completed"] is True
    assert body["elaboration"] == "Testing"
    assert body["answers"] == {a["question_id"]: a["answer_value"] for a in answers}

    user = _get_user(token)
    assert user.risk_tolerance == expected_bucket
    assert user.investment_goals == "Testing"

    db = SessionLocal()
    try:
        rows = (
            db.query(RiskQuestionnaireResponse)
            .filter(RiskQuestionnaireResponse.user_id == user.id)
            .all()
        )
        assert len(rows) == len(QUESTIONS)
        assert sum(r.points for r in rows) == points * len(QUESTIONS)
    finally:
        db.close()


def test_bucket_thresholds_match_even_thirds_of_score_range():
    # Documents/locks the exact thresholds so a future question-bank
    # change that shifts MIN/MAX_SCORE gets caught here rather than
    # silently reshaping the buckets.
    assert (MIN_SCORE, MAX_SCORE) == (12, 60)
    assert (LOW_MAX, HIGH_MIN) == (28, 45)
    assert bucket_for_score(LOW_MAX) == "low"
    assert bucket_for_score(LOW_MAX + 1) == "medium"
    assert bucket_for_score(HIGH_MIN - 1) == "medium"
    assert bucket_for_score(HIGH_MIN) == "high"


# --- prefill on reopen / resubmission ------------------------------------------


def test_get_state_after_submission_returns_saved_answers(client):
    token = "onboarding-test-prefill-user"
    answers = _answers_at_points(3)
    client.post(
        "/onboarding",
        json={"answers": answers, "elaboration": "Saving for a house"},
        headers=_auth(token),
    )

    resp = client.get("/onboarding", headers=_auth(token))
    assert resp.status_code == 200
    body = resp.json()
    assert body["completed"] is True
    assert body["answers"] == {a["question_id"]: a["answer_value"] for a in answers}
    assert body["elaboration"] == "Saving for a house"
    assert body["risk_tolerance"] == "medium"


def test_resubmission_replaces_rather_than_accumulates(client):
    token = "onboarding-test-resubmit-user"
    client.post(
        "/onboarding",
        json={"answers": _answers_at_points(1), "elaboration": "first pass"},
        headers=_auth(token),
    )
    resp = client.post(
        "/onboarding",
        json={"answers": _answers_at_points(5), "elaboration": None},
        headers=_auth(token),
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["risk_tolerance"] == "high"
    assert body["elaboration"] is None

    user = _get_user(token)
    assert user.risk_tolerance == "high"
    assert user.investment_goals is None

    db = SessionLocal()
    try:
        rows = (
            db.query(RiskQuestionnaireResponse)
            .filter(RiskQuestionnaireResponse.user_id == user.id)
            .all()
        )
        # Still exactly one row per question — resubmission replaced the
        # old set rather than appending a second copy.
        assert len(rows) == len(QUESTIONS)
        assert all(r.points == 5 for r in rows)
    finally:
        db.close()


# --- validation ------------------------------------------


def test_submission_missing_a_question_is_422(client):
    answers = _answers_at_points(3)[:-1]  # drop one
    resp = client.post(
        "/onboarding",
        json={"answers": answers},
        headers=_auth("onboarding-test-missing-user"),
    )
    assert resp.status_code == 422


def test_submission_with_unknown_question_id_is_422(client):
    answers = _answers_at_points(3) + [{"question_id": "not_real", "answer_value": "x"}]
    resp = client.post(
        "/onboarding",
        json={"answers": answers},
        headers=_auth("onboarding-test-unknown-user"),
    )
    assert resp.status_code == 422


def test_submission_with_invalid_answer_value_is_422(client):
    answers = _answers_at_points(3)
    answers[0] = {"question_id": answers[0]["question_id"], "answer_value": "not_a_real_option"}
    resp = client.post(
        "/onboarding",
        json={"answers": answers},
        headers=_auth("onboarding-test-badvalue-user"),
    )
    assert resp.status_code == 422


# --- downstream contract: recommendation_engine's tier lookup ------------------------------------------


def test_high_bucket_still_maps_to_aggressive_tier_downstream():
    """Proves the questionnaire rebuild didn't break
    ai.recommendation_engine.py's tier lookup — it must keep resolving
    the exact same low/medium/high strings to
    conservative/moderate/aggressive with zero changes on that side."""
    from app.ai.recommendation_engine import _tier_for

    assert _tier_for("low") == "conservative"
    assert _tier_for("medium") == "moderate"
    assert _tier_for("high") == "aggressive"


def test_end_to_end_questionnaire_bucket_resolves_correct_tier(client):
    token = "onboarding-test-e2e-tier-user"
    client.post(
        "/onboarding",
        json={"answers": _answers_at_points(5)},
        headers=_auth(token),
    )
    user = _get_user(token)
    assert user.risk_tolerance == "high"

    from app.ai.recommendation_engine import _tier_for
    from agent.news_ingestion import TARGET_PORTFOLIOS

    tier = _tier_for(user.risk_tolerance)
    assert tier == "aggressive"
    assert tier in TARGET_PORTFOLIOS  # the candidate ticker set this tier would recommend from
