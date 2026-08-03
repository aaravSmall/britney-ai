from fastapi import APIRouter

from app.deps import CurrentUser, DbSession
from app.schemas.dashboard import DashboardResponse
from app.services.portfolio_service import build_dashboard

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
async def get_dashboard(db: DbSession, user: CurrentUser) -> DashboardResponse:
    return await build_dashboard(db, user)
