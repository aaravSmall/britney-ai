"""CRUD for recurring auto-invest schedules — fixed $ amount into one
ticker on a daily/weekly/monthly cadence. Every row is private to one
user (same posture as favorites.py: no public/agent equivalent), so
every endpoint requires CurrentUser with no optional-auth path.

Schedules are only ever created against the caller's own portfolio
(resolved server-side via get_or_create_portfolio) — there's no way to
target an agent portfolio through this router. Actual execution happens
in the separate backend/agent/run_auto_invest.py loop via
app/services/auto_invest_service.py, not here.
"""

from fastapi import APIRouter, HTTPException

from app.deps import CurrentUser, DbSession
from app.models import AutoInvestSchedule
from app.schemas.auto_invest import CreateScheduleRequest, ScheduleOut, UpdateScheduleRequest
from app.services.portfolio_service import get_or_create_portfolio

router = APIRouter(prefix="/auto-invest/schedules", tags=["auto-invest"])


def _get_owned_schedule(db, schedule_id: int, user) -> AutoInvestSchedule:
    schedule = db.get(AutoInvestSchedule, schedule_id)
    if schedule is None or schedule.user_id != user.id:
        raise HTTPException(status_code=404, detail="Schedule not found")
    return schedule


@router.post("", response_model=ScheduleOut, status_code=201)
def create_schedule(
    body: CreateScheduleRequest,
    db: DbSession,
    user: CurrentUser,
) -> AutoInvestSchedule:
    portfolio = get_or_create_portfolio(db, user)
    schedule = AutoInvestSchedule(
        user_id=user.id,
        portfolio_id=portfolio.id,
        ticker=body.ticker.upper(),
        asset_type=body.asset_type,
        amount=body.amount,
        interval=body.interval,
    )
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.get("", response_model=list[ScheduleOut])
def list_schedules(db: DbSession, user: CurrentUser) -> list[AutoInvestSchedule]:
    return (
        db.query(AutoInvestSchedule)
        .filter(AutoInvestSchedule.user_id == user.id)
        .order_by(AutoInvestSchedule.created_at.desc())
        .all()
    )


@router.patch("/{schedule_id}", response_model=ScheduleOut)
def update_schedule(
    schedule_id: int,
    body: UpdateScheduleRequest,
    db: DbSession,
    user: CurrentUser,
) -> AutoInvestSchedule:
    schedule = _get_owned_schedule(db, schedule_id, user)
    if body.enabled is not None:
        schedule.enabled = body.enabled
    if body.amount is not None:
        schedule.amount = body.amount
    if body.interval is not None:
        schedule.interval = body.interval
    db.add(schedule)
    db.commit()
    db.refresh(schedule)
    return schedule


@router.delete("/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, db: DbSession, user: CurrentUser) -> None:
    schedule = _get_owned_schedule(db, schedule_id, user)
    db.delete(schedule)
    db.commit()
