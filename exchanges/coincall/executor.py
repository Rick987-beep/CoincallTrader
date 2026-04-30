"""Coincall executor adapter — wraps existing TradeExecutor.

Translates string side ('buy'/'sell') to Coincall int side (1/2)
at the API boundary.
"""


def _snap_qty(qty: float) -> float:
    """
    Snap a contract quantity to Coincall's minimum lot size (0.01 contracts).

    Coincall requires quantities to be a multiple of 0.01. When the order
    manager computes a remaining qty after a partial fill, IEEE 754
    arithmetic can produce values like 0.5999999999999996 instead of 0.6.
    Rounding here at the outbound boundary, mirroring how the Deribit
    adapter normalises prices via _snap_to_tick, ensures the exchange
    always receives a clean value regardless of how the qty was derived.
    """
    return round(round(qty / 0.01) * 0.01, 2)

from exchanges.base import ExchangeExecutor
from trade_execution import TradeExecutor


def _side_to_int(side: str) -> int:
    """Convert normalized string side to Coincall int encoding."""
    return 1 if side == "buy" else 2


class CoincallExecutorAdapter(ExchangeExecutor):
    """Wraps TradeExecutor, translating string sides to int."""

    def __init__(self):
        self._inner = TradeExecutor()

    def place_order(self, symbol, qty, side, order_type=1, price=None,
                    client_order_id=None, reduce_only=False):
        return self._inner.place_order(
            symbol=symbol,
            qty=_snap_qty(qty),
            side=_side_to_int(side),
            order_type=order_type,
            price=price,
            client_order_id=client_order_id,
            reduce_only=reduce_only,
        )

    def cancel_order(self, order_id):
        return self._inner.cancel_order(order_id)

    def get_order_status(self, order_id):
        return self._inner.get_order_status(order_id)
