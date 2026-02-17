#!/usr/bin/env python3
"""
Trade Execution Module — Transport & Fill Management Layer

Provides:
  1. TradeExecutor  — thin API client (place, cancel, query orders)
  2. ExecutionParams — per-trade fill-management configuration
  3. LimitFillManager — tracks a set of pending leg orders, polls fills,
     and requotes on timeout.  Used by trade_lifecycle for "limit" mode.

Environment-agnostic — works the same for testnet and production.
The environment is controlled via config.py.
"""

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Any
from config import BASE_URL, API_KEY, API_SECRET
from auth import CoincallAuth
from market_data import get_option_orderbook

logger = logging.getLogger(__name__)


class TradeExecutor:
    """Executes trades and manages orders"""

    def __init__(self):
        """Initialize trade executor with authenticated API client"""
        self.auth = CoincallAuth(API_KEY, API_SECRET, BASE_URL)

    def place_order(
        self,
        symbol: str,
        qty: float,
        side: int,
        order_type: int = 1,
        price: Optional[float] = None,
        client_order_id: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Place a single order. Returns dict with orderId or None on error."""
        try:
            payload = {
                'symbol': symbol,
                'qty': qty,
                'tradeSide': side,
                'tradeType': order_type,
            }
            
            if price is not None:
                payload['price'] = price
            
            if client_order_id:
                payload['clientOrderId'] = int(client_order_id)
            
            response = self.auth.post('/open/option/order/create/v1', payload)
            
            if self.auth.is_successful(response):
                order_id = response.get('data')
                logger.info(f"Order placed: {order_id} for {symbol}")
                return {'orderId': order_id}
            else:
                logger.error(f"Order failed for {symbol}: {response.get('msg')}")
                return None
        
        except Exception as e:
            logger.error(f"Exception placing order for {symbol}: {e}")
            return None

    def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an order by ID
        
        Args:
            order_id: Order ID to cancel
            
        Returns:
            True if cancelled successfully
        """
        try:
            response = self.auth.post('/open/option/order/cancel/v1', {'orderId': int(order_id)})
            
            if self.auth.is_successful(response):
                logger.info(f"Order cancelled: {order_id}")
                return True
            else:
                logger.error(f"Failed to cancel order {order_id}: {response.get('msg')}")
                return False
        
        except Exception as e:
            logger.error(f"Exception cancelling order {order_id}: {e}")
            return False

    def get_order_status(self, order_id: str) -> Optional[Dict[str, Any]]:
        """
        Get order status by ID.

        Uses the singleQuery endpoint:
            GET /open/option/order/singleQuery/v1?orderId={id}

        Returns fields like orderId, symbol, qty, fillQty, remainQty,
        price, avgPrice, state, tradeSide, etc.

        State enum (options):
            0=NEW, 1=FILLED, 2=PARTIALLY_FILLED, 3=CANCELED,
            4=PRE_CANCEL, 5=CANCELING, 6=INVALID, 10=CANCEL_BY_EXERCISE

        Args:
            order_id: Order ID

        Returns:
            Order information dict or None on error
        """
        try:
            response = self.auth.get(f'/open/option/order/singleQuery/v1?orderId={order_id}')

            if self.auth.is_successful(response):
                return response.get('data', {})
            else:
                logger.error(f"Failed to get order status for {order_id}: {response.get('msg')}")
                return None

        except Exception as e:
            logger.error(f"Exception getting order status for {order_id}: {e}")
            return None


# =============================================================================
# Execution Parameters — configurable per-trade
# =============================================================================

@dataclass
class ExecutionParams:
    """
    Per-trade fill-management configuration.

    Strategies set these at trade-creation time to control how aggressively
    orders are filled.  Stored on TradeLifecycle so the LimitFillManager
    can read them.

    Attributes:
        fill_timeout_seconds: Seconds before cancelling and requoting unfilled orders.
        aggressive_buffer_pct: % beyond best price (buy: ask×1.02, sell: bid/1.02).
        max_requote_rounds: Give up and fail after this many requote cycles.
    """
    fill_timeout_seconds: float = 30.0
    aggressive_buffer_pct: float = 2.0
    max_requote_rounds: int = 10


# =============================================================================
# Limit Fill Manager — tracks pending orders, polls fills, requotes on timeout
# =============================================================================

@dataclass
class _LegFillState:
    """Internal: tracks one leg's order and fill progress."""
    symbol: str
    qty: float
    side: int          # 1=buy, 2=sell
    order_id: Optional[str] = None
    filled_qty: float = 0.0
    fill_price: Optional[float] = None
    requote_count: int = 0
    started_at: float = field(default_factory=time.time)

    @property
    def is_filled(self) -> bool:
        return self.filled_qty >= self.qty

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.qty - self.filled_qty)

    @property
    def side_label(self) -> str:
        return "buy" if self.side == 1 else "sell"


class LimitFillManager:
    """
    Manages fill detection and requoting for a set of limit-order legs.

    Lifecycle:
      1. Caller creates the manager with an executor + params.
      2. ``place_all(legs)`` places initial orders for every leg.
      3. Each tick, caller invokes ``check()`` which:
         a. Polls order status for every unfilled leg.
         b. If all filled → returns ``"filled"``.
         c. If timeout elapsed → cancels stale orders, re-places at
            fresh aggressive prices → returns ``"requoted"``.
         d. If max requote rounds exhausted → returns ``"failed"``.
         e. Otherwise → returns ``"pending"``.
      4. ``cancel_all()`` cancels any outstanding orders (for cleanup).
      5. ``filled_legs`` returns the final fill details.

    This class does NOT own the TradeLifecycle state machine — it is a
    helper that trade_lifecycle drives via its tick loop.
    """

    def __init__(self, executor: "TradeExecutor", params: Optional[ExecutionParams] = None):
        self._executor = executor
        self._params = params or ExecutionParams()
        self._legs: List[_LegFillState] = []
        self._round_started_at: float = time.time()

    # -- Public API -----------------------------------------------------------

    def place_all(self, legs: List[Dict[str, Any]]) -> bool:
        """
        Place initial limit orders for all legs.

        Args:
            legs: List of dicts with keys: symbol, qty, side, order_id (out).
                  Each dict is a TradeLeg-like object (duck-typed).

        Returns:
            True if all orders placed successfully.
            On failure, already-placed orders are cancelled.
        """
        self._legs = []
        self._round_started_at = time.time()

        for leg in legs:
            symbol = leg.symbol if hasattr(leg, 'symbol') else leg['symbol']
            qty = leg.qty if hasattr(leg, 'qty') else leg['qty']
            side = leg.side if hasattr(leg, 'side') else leg['side']

            price = self._get_aggressive_price(symbol, side)
            if price is None:
                side_label = "buy" if side == 1 else "sell"
                logger.error(f"LimitFillManager: no orderbook price for {symbol} ({side_label})")
                self.cancel_all()
                return False

            result = self._executor.place_order(
                symbol=symbol, qty=qty, side=side, order_type=1, price=price,
            )
            if not result:
                logger.error(f"LimitFillManager: failed to place order for {symbol}")
                self.cancel_all()
                return False

            state = _LegFillState(
                symbol=symbol, qty=qty, side=side,
                order_id=str(result.get('orderId', '')),
            )
            self._legs.append(state)

            # Write order_id back to the caller's leg object
            if hasattr(leg, 'order_id'):
                leg.order_id = state.order_id
            side_label = state.side_label
            logger.info(
                f"LimitFillManager: placed {side_label} {qty}x {symbol} @ ${price} "
                f"(order {state.order_id})"
            )

        logger.info(f"LimitFillManager: all {len(self._legs)} orders placed, awaiting fills")
        return True

    def check(self) -> str:
        """
        Poll fills and handle timeouts.  Call once per tick.

        Returns:
            "filled"   — all legs filled
            "requoted" — timeout hit, unfilled orders cancelled and re-placed
            "failed"   — max requote rounds exhausted or unrecoverable error
            "pending"  — still waiting for fills
        """
        # 1. Poll each unfilled leg
        for ls in self._legs:
            if ls.is_filled or not ls.order_id:
                continue
            try:
                info = self._executor.get_order_status(ls.order_id)
                if info:
                    executed = float(info.get('fillQty', 0))
                    if executed > ls.filled_qty:
                        ls.filled_qty = executed
                        ls.fill_price = float(info.get('avgPrice', 0)) or ls.fill_price
                        logger.info(
                            f"LimitFillManager: {ls.symbol} filled "
                            f"{ls.filled_qty}/{ls.qty} @ {ls.fill_price}"
                        )
                    # Detect externally-cancelled orders
                    state_code = info.get('state')
                    if state_code == 3 and not ls.is_filled:
                        logger.warning(
                            f"LimitFillManager: {ls.symbol} order {ls.order_id} was cancelled externally "
                            f"(filled {ls.filled_qty}/{ls.qty})"
                        )
            except Exception as e:
                logger.error(f"LimitFillManager: error checking {ls.order_id}: {e}")

        # 2. All filled?
        if all(ls.is_filled for ls in self._legs):
            return "filled"

        # 3. Timeout check → requote
        elapsed = time.time() - self._round_started_at
        if elapsed > self._params.fill_timeout_seconds:
            # Check if any leg has exhausted requote rounds
            unfilled = [ls for ls in self._legs if not ls.is_filled]
            if any(ls.requote_count >= self._params.max_requote_rounds for ls in unfilled):
                logger.error(
                    f"LimitFillManager: max requote rounds "
                    f"({self._params.max_requote_rounds}) exhausted"
                )
                return "failed"

            logger.warning(
                f"LimitFillManager: timeout ({elapsed:.0f}s > "
                f"{self._params.fill_timeout_seconds}s) — requoting unfilled legs"
            )
            self._requote_unfilled()
            return "requoted"

        return "pending"

    def cancel_all(self) -> None:
        """Cancel any outstanding unfilled orders."""
        for ls in self._legs:
            if ls.order_id and not ls.is_filled:
                try:
                    self._executor.cancel_order(ls.order_id)
                    logger.info(f"LimitFillManager: cancelled {ls.order_id} for {ls.symbol}")
                except Exception as e:
                    logger.warning(f"LimitFillManager: cancel failed for {ls.order_id}: {e}")

    @property
    def all_filled(self) -> bool:
        return all(ls.is_filled for ls in self._legs)

    @property
    def filled_legs(self) -> List[_LegFillState]:
        """Read-only access to leg states (for extracting fill details)."""
        return list(self._legs)

    @property
    def partially_filled_legs(self) -> List[_LegFillState]:
        """Legs that have some but not all qty filled."""
        return [ls for ls in self._legs if ls.filled_qty > 0 and not ls.is_filled]

    @property
    def unfilled_legs(self) -> List[_LegFillState]:
        """Legs with zero fills."""
        return [ls for ls in self._legs if ls.filled_qty == 0]

    # -- Internal -------------------------------------------------------------

    def _requote_unfilled(self) -> None:
        """Cancel stale orders and re-place at fresh aggressive prices."""
        self._round_started_at = time.time()  # reset timeout for next round

        for ls in self._legs:
            if ls.is_filled:
                continue
            if not ls.order_id:
                continue

            # Cancel stale order
            try:
                self._executor.cancel_order(ls.order_id)
                logger.info(f"LimitFillManager: cancelled stale order {ls.order_id} for {ls.symbol}")
            except Exception as e:
                logger.warning(f"LimitFillManager: cancel failed for {ls.order_id}: {e}")

            # Re-place at fresh price
            try:
                price = self._get_aggressive_price(ls.symbol, ls.side)
                if price is None:
                    logger.error(f"LimitFillManager: no price for {ls.symbol} on requote")
                    continue
                result = self._executor.place_order(
                    symbol=ls.symbol,
                    qty=ls.remaining_qty,
                    side=ls.side,
                    order_type=1,
                    price=price,
                )
                if result:
                    ls.order_id = str(result.get('orderId', ''))
                    ls.requote_count += 1
                    logger.info(
                        f"LimitFillManager: requoted {ls.side_label} "
                        f"{ls.remaining_qty}x {ls.symbol} @ ${price} "
                        f"(round {ls.requote_count})"
                    )
                else:
                    logger.error(f"LimitFillManager: requote failed for {ls.symbol}")
            except Exception as e:
                logger.error(f"LimitFillManager: requote exception for {ls.symbol}: {e}")

    def _get_aggressive_price(self, symbol: str, side: int) -> Optional[float]:
        """Fetch best bid/ask and apply aggressive buffer."""
        try:
            ob = get_option_orderbook(symbol)
            if not ob:
                return None

            buffer = 1 + (self._params.aggressive_buffer_pct / 100.0)

            if side == 1 and ob.get('asks'):
                raw = float(ob['asks'][0]['price'])
                return round(raw * buffer, 2)
            elif side == 2 and ob.get('bids'):
                raw = float(ob['bids'][0]['price'])
                return round(raw / buffer, 2)

            return None
        except Exception as e:
            logger.error(f"LimitFillManager: error fetching price for {symbol}: {e}")
            return None
