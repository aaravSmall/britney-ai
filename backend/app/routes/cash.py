"""Deposit/withdraw simulated cash into a user's own portfolio.

Both endpoints go through cash_ledger.apply_cash_delta so every balance
change gets an audit-trail CashLedgerEntry, same as a trade fill.

Agent portfolios (owner_type="agent") reject deposit/withdraw outright:
unlike portfolios.py's read endpoints (public for agent portfolios,
owner-only for user portfolios), there's no owning user to authorize a
write against, and no reason a simulated-cash top-up should ever apply
to the agent's own model portfolios.
"""

from typing import Annotated

from fastapi import APIRouter, Header, HTTPException

from app.deps import DbSession, resolve_current_user
from app.models import Portfolio
from app.schemas.cash import CashAmountRequest
from app.schemas.dashboard import DashboardResponse
from app.services.cash_ledger import apply_cash_delta
from app.services.portfolio_service import InsufficientFundsError, build_portfolio_summary

router = APIRouter(prefix="/portfolios", tags=["cash"])


def _require_own_user_portfolio(
    db,
    portfolio: Portfolio,
    authorization: str | None,
) -> None:
    """403 if this isn't a user-owned portfolio at all (agent portfolios
    have no owner to authorize against), or if it belongs to a different
    user than the token presented. Always requires a valid token — unlike
    _require_owner_if_user_portfolio (portfolios.py), there's no
    public-read case here since this gate only guards writes."""
    if portfolio.owner_type != "user":
        raise HTTPException(
            status_code=403,
            detail="Agent portfolios cannot be deposited to or withdrawn from",
        )
    current_user = resolve_current_user(db, authorization)
    if portfolio.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not your portfolio")


@router.post("/{portfolio_id}/deposit", response_model=DashboardResponse)
async def deposit_cash(
    portfolio_id: int,
    body: CashAmountRequest,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> DashboardResponse:
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    _require_own_user_portfolio(db, portfolio, authorization)

    apply_cash_delta(db, portfolio, body.amount, "deposit", note=body.note)
    db.commit()
    db.refresh(portfolio)
    return await build_portfolio_summary(db, portfolio)


@router.post("/{portfolio_id}/withdraw", response_model=DashboardResponse)
async def withdraw_cash(
    portfolio_id: int,
    body: CashAmountRequest,
    db: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> DashboardResponse:
    portfolio = db.get(Portfolio, portfolio_id)
    if portfolio is None:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    _require_own_user_portfolio(db, portfolio, authorization)

    try:
        if body.amount > portfolio.cash_balance:
            raise InsufficientFundsError(
                f"Insufficient cash: need ${body.amount:,.2f}, "
                f"have ${portfolio.cash_balance:,.2f}"
            )
        apply_cash_delta(db, portfolio, -body.amount, "withdrawal", note=body.note)
    except InsufficientFundsError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    db.commit()
    db.refresh(portfolio)
    return await build_portfolio_summary(db, portfolio)
