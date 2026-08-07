"""Per-user favorited stock tickers. Unlike app/routes/stocks.py's public
market-data endpoints, every row here is inherently private to one user
— there's no agent/public equivalent the way Portfolio has — so every
endpoint requires a valid auth token via CurrentUser (app/deps.py) with
no optional-auth path at all, matching the Sprint 2 fix's underlying
resolve_current_user primitive (portfolios.py's
_require_owner_if_user_portfolio layers a public/private branch on top
of that same primitive for portfolios' mixed-visibility rows; favorites
don't need that branch since they're never public)."""

import asyncio

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import IntegrityError

from app.deps import CurrentUser, DbSession
from app.models import Favorite
from app.schemas.favorites import FavoriteAdded, FavoriteOut
from app.services import stock_data

router = APIRouter(prefix="/favorites", tags=["favorites"])


@router.post("/{ticker}", response_model=FavoriteAdded, status_code=201)
def add_favorite(ticker: str, db: DbSession, user: CurrentUser) -> Favorite:
    key = ticker.upper()
    existing = (
        db.query(Favorite)
        .filter(Favorite.user_id == user.id, Favorite.ticker == key)
        .one_or_none()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail=f"{key} is already a favorite")

    favorite = Favorite(user_id=user.id, ticker=key)
    db.add(favorite)
    try:
        db.commit()
    except IntegrityError:
        # The unique constraint is the real guard against a duplicate
        # row — this just turns a lost race (another request for the
        # same user+ticker committing between our check and our commit)
        # into the same 409 instead of a raw 500.
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"{key} is already a favorite"
        ) from None
    db.refresh(favorite)
    return favorite


@router.delete("/{ticker}", status_code=204)
def remove_favorite(ticker: str, db: DbSession, user: CurrentUser) -> None:
    key = ticker.upper()
    existing = (
        db.query(Favorite)
        .filter(Favorite.user_id == user.id, Favorite.ticker == key)
        .one_or_none()
    )
    if existing is None:
        raise HTTPException(status_code=404, detail=f"{key} is not a favorite")
    db.delete(existing)
    db.commit()


@router.get("", response_model=list[FavoriteOut])
async def list_favorites(db: DbSession, user: CurrentUser) -> list[dict]:
    """Reuses stock_data.quote() (app/services/stock_data.py, from the
    stock search/detail feature's step 1) rather than duplicating any
    quote-fetching logic. Each favorite's quote() call is already async
    (its blocking yfinance call runs via run_in_threadpool) and
    independently TTL-cached per ticker, so gathering them concurrently
    here is the easy, non-duplicative way to avoid N sequential
    round-trips without writing new batch-fetch logic in stock_data.py."""
    favorites = (
        db.query(Favorite)
        .filter(Favorite.user_id == user.id)
        .order_by(Favorite.created_at.desc())
        .all()
    )
    if not favorites:
        return []

    quotes = await asyncio.gather(*(stock_data.quote(f.ticker) for f in favorites))
    return [
        {
            "ticker": fav.ticker,
            "favorited_at": fav.created_at,
            "company_name": q["company_name"] if q else None,
            "price": q["price"] if q else None,
            "change": q["change"] if q else None,
            "change_pct": q["change_pct"] if q else None,
        }
        for fav, q in zip(favorites, quotes)
    ]
