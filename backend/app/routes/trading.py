from fastapi import APIRouter, HTTPException

from app.deps import CurrentUser, DbSession
from app.schemas.trading import TradeRequest, TradeResponse
from app.services import market_data
from app.services.portfolio_service import (
    InsufficientFundsError,
    InsufficientHoldingsError,
    record_trade_fill,
)
from app.trading.execution import execute_trade

router = APIRouter(prefix="/trading", tags=["trading"])


@router.post("/trade", response_model=TradeResponse)
async def place_trade(
    body: TradeRequest,
    db: DbSession,
    user: CurrentUser,
) -> TradeResponse:
    price = await market_data.get_price_for_holding(body.symbol, body.asset_type)
    if price is None:
        raise HTTPException(status_code=400, detail=f"No price available for {body.symbol}")

    # Respect auto-invest: if off, only allow simulate
    simulate = body.simulate_only or not user.auto_invest_enabled
    result = execute_trade(
        body.symbol,
        body.asset_type,
        body.side,
        body.quantity,
        simulate_only=simulate,
    )

    if result.status == "filled":
        try:
            record_trade_fill(
                db, user, body.symbol, body.asset_type, body.side, body.quantity, price
            )
        except (InsufficientFundsError, InsufficientHoldingsError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return TradeResponse(
        status=result.status,
        simulated=result.simulated,
        message=result.message,
        order_id=result.order_id,
    )
