from pydantic import BaseModel

from fastapi import APIRouter

from app.deps import CurrentUser, DbSession

router = APIRouter(prefix="/settings", tags=["settings"])


class AutoInvestBody(BaseModel):
    enabled: bool


@router.patch("/auto-invest")
def set_auto_invest(
    body: AutoInvestBody,
    db: DbSession,
    user: CurrentUser,
) -> dict[str, bool]:
    user.auto_invest_enabled = body.enabled
    db.add(user)
    db.commit()
    return {"auto_invest_enabled": user.auto_invest_enabled}
