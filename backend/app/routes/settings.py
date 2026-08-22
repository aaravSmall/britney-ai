from zoneinfo import available_timezones

from pydantic import BaseModel, field_validator

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


# "device" means "render in whatever timezone the client's device is in" —
# not a real IANA zone, so it's allowed alongside zoneinfo's own set rather
# than validated against it. Computed once at import rather than per
# request since zoneinfo.available_timezones() scans the tzdata package.
_VALID_TIMEZONES = {"device"} | available_timezones()


class TimezoneBody(BaseModel):
    timezone: str

    @field_validator("timezone")
    @classmethod
    def _validate_timezone(cls, v: str) -> str:
        if v not in _VALID_TIMEZONES:
            raise ValueError(f"Unknown timezone: {v}")
        return v


@router.patch("/timezone")
def set_timezone(
    body: TimezoneBody,
    db: DbSession,
    user: CurrentUser,
) -> dict[str, str]:
    user.timezone = body.timezone
    db.add(user)
    db.commit()
    return {"timezone": user.timezone}
