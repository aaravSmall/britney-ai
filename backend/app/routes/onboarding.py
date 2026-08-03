from fastapi import APIRouter

from app.deps import CurrentUser, DbSession
from app.schemas.onboarding import OnboardingUpdate
from app.schemas.user import UserOut

router = APIRouter(prefix="/onboarding", tags=["onboarding"])


@router.post("", response_model=UserOut)
def save_onboarding(
    body: OnboardingUpdate,
    db: DbSession,
    user: CurrentUser,
) -> UserOut:
    user.risk_tolerance = body.risk_tolerance
    user.investment_goals = body.investment_goals
    user.time_horizon = body.time_horizon
    db.add(user)
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)
