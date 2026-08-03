from fastapi import APIRouter

from app.deps import CurrentUser, DbSession
from app.schemas.trading import TradeRequest, TradeResponse
from app.trading.execution import execute_trade

router = APIRouter(prefix="/trading", tags=["trading"])


@router.post("/trade", response_model=TradeResponse)
def place_trade(
    body: TradeRequest,
    db: DbSession,
    user: CurrentUser,
) -> TradeResponse:
    # Respect auto-invest: if off, only allow simulate
    simulate = body.simulate_only or not user.auto_invest_enabled
    result = execute_trade(
        body.symbol,
        body.asset_type,
        body.side,
        body.quantity,
        simulate_only=simulate,
    )
    return TradeResponse(
        status=result.status,
        simulated=result.simulated,
        message=result.message,
        order_id=result.order_id,
    )
