"""
Trade execution abstraction: simulate now, plug Alpaca (or others) later.
"""

import uuid
from dataclasses import dataclass

from app.config import get_settings


@dataclass
class TradeResult:
    status: str
    simulated: bool
    message: str
    order_id: str | None


def simulate_trade(
    symbol: str,
    asset_type: str,
    side: str,
    quantity: float,
) -> TradeResult:
    """Paper-trade simulation — no external IO."""
    oid = f"sim-{uuid.uuid4().hex[:12]}"
    return TradeResult(
        status="filled",
        simulated=True,
        message=(
            f"Simulated {side.upper()} {quantity} {symbol} ({asset_type}) — "
            "paper mode; connect Alpaca keys for live paper API."
        ),
        order_id=oid,
    )


def execute_trade(
    symbol: str,
    asset_type: str,
    side: str,
    quantity: float,
    simulate_only: bool,
) -> TradeResult:
    """
    If simulate_only or Alpaca not configured, always simulate.
    Real routing can call Alpaca REST here when keys + user consent exist.
    """
    settings = get_settings()
    if simulate_only or not (
        settings.alpaca_api_key and settings.alpaca_secret_key
    ):
        return simulate_trade(symbol, asset_type, side, quantity)

    # Placeholder: production would submit order and return broker id
    return TradeResult(
        status="not_implemented",
        simulated=False,
        message="Live execution path reserved; use simulate_only for MVP.",
        order_id=None,
    )
