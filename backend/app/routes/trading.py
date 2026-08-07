from fastapi import APIRouter, HTTPException, Query

from app.deps import CurrentUser, DbSession
from app.schemas.trading import PriceOut, TradeRequest, TradeResponse
from app.services import market_data
from app.services.portfolio_service import (
    InsufficientFundsError,
    InsufficientHoldingsError,
    get_or_create_portfolio,
    record_trade_fill,
)
from app.trading.execution import execute_trade

router = APIRouter(prefix="/trading", tags=["trading"])


@router.get("/price", response_model=PriceOut)
async def get_price(
    user: CurrentUser,
    symbol: str,
    asset_type: str = Query(default="stock", pattern="^(stock|crypto|option)$"),
) -> PriceOut:
    """Live price lookup for the buy/sell sheet's $ ⇄ shares conversion and
    trade preview — read-only, doesn't touch execution/persistence."""
    price = await market_data.get_price_for_holding(symbol, asset_type)
    if price is None:
        raise HTTPException(status_code=400, detail=f"No price available for {symbol}")
    return PriceOut(symbol=symbol.upper(), asset_type=asset_type, price=price)


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
            portfolio = get_or_create_portfolio(db, user)
            record_trade_fill(
                db,
                portfolio,
                body.symbol,
                body.asset_type,
                body.side,
                body.quantity,
                price,
                simulated=result.simulated,
                order_id=result.order_id,
                source="user",
            )
        except (InsufficientFundsError, InsufficientHoldingsError) as e:
            raise HTTPException(status_code=400, detail=str(e)) from e

    return TradeResponse(
        status=result.status,
        simulated=result.simulated,
        message=result.message,
        order_id=result.order_id,
    )
