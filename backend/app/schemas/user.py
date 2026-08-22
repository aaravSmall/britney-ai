from pydantic import BaseModel, EmailStr


class UserCreate(BaseModel):
    email: EmailStr
    name: str | None = None


class UserOut(BaseModel):
    id: int
    email: str
    name: str | None
    risk_tolerance: str | None
    investment_goals: str | None
    time_horizon: str | None
    auto_invest_enabled: bool
    timezone: str

    model_config = {"from_attributes": True}
