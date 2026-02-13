#!/usr/bin/env python3
"""
Trade Lifecycle Manager

Orchestrates the full lifecycle of a trade from intent through execution,
position management, and closing:

    PENDING_OPEN → OPENING → OPEN → PENDING_CLOSE → CLOSING → CLOSED

Each TradeLifecycle groups one or more legs (e.g. an Iron Condor has 4 legs).
The LifecycleManager advances every active trade through the state machine on
each tick(), which is driven by the PositionMonitor callback.

Supports three execution modes:
  - "limit"  : per-leg limit orders via TradeExecutor (parallel, with requoting)
  - "rfq"    : atomic multi-leg RFQ via RFQExecutor
  - "smart"  : multi-leg smart orderbook with chunking & continuous quoting

Exit conditions are callables with signature:
    (AccountSnapshot, TradeLifecycle) -> bool
Factory functions are provided for common patterns (profit target, max loss, etc.).
"""

import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from account_manager import AccountSnapshot, PositionMonitor, PositionSnapshot
from trade_execution import TradeExecutor
from rfq import RFQExecutor, OptionLeg, RFQResult
from multileg_orderbook import SmartOrderbookExecutor, SmartExecConfig
from market_data import get_option_orderbook

logger = logging.getLogger(__name__)


# =============================================================================
# Enums & Data Classes
# =============================================================================

class TradeState(Enum):
    """States in the trade lifecycle state machine."""
    PENDING_OPEN  = "pending_open"   # Intent created, no orders yet
    OPENING       = "opening"        # Open orders placed, waiting for fills
    OPEN          = "open"           # All legs filled, position being managed
    PENDING_CLOSE = "pending_close"  # Exit triggered, not yet ordered
    CLOSING       = "closing"        # Close orders placed, waiting for fills
    CLOSED        = "closed"         # Fully closed
    FAILED        = "failed"         # Unrecoverable error


@dataclass
class TradeLeg:
    """
    A single leg within a trade lifecycle.

    Fields are populated progressively:
      - symbol/qty/side are set at creation
      - order_id is set when the order is placed
      - fill_price is set when the order fills
      - position_id is set when the position appears on the exchange
    """
    symbol: str
    qty: float
    side: int               # 1 = buy, 2 = sell

    # Populated after order placement
    order_id: Optional[str] = None

    # Populated after fill
    fill_price: Optional[float] = None
    filled_qty: float = 0.0

    # Populated when matched to exchange position
    position_id: Optional[str] = None

    @property
    def is_filled(self) -> bool:
        return self.filled_qty >= self.qty

    @property
    def close_side(self) -> int:
        """Opposite side for closing this leg."""
        return 2 if self.side == 1 else 1

    @property
    def side_label(self) -> str:
        return "buy" if self.side == 1 else "sell"


# Type alias for exit condition callables
ExitCondition = Callable[[AccountSnapshot, "TradeLifecycle"], bool]


@dataclass
class TradeLifecycle:
    """
    Tracks one trade (possibly multi-leg) from intent through close.

    Attributes:
        id:               Unique identifier (UUID)
        state:            Current lifecycle state
        open_legs:        Legs for opening the position
        close_legs:       Legs for closing (auto-generated as reverse of open)
        exit_conditions:  List of callables; if ANY returns True, trigger close
        execution_mode:   "limit", "rfq", "smart", or None (auto-route)
        rfq_action:       "buy" or "sell" — passed to RFQExecutor.execute()
        smart_config:     SmartExecConfig for "smart" mode execution
        created_at:       Unix timestamp of creation
        opened_at:        Unix timestamp when all open legs filled
        closed_at:        Unix timestamp when all close legs filled
        error:            Error message if state is FAILED
        rfq_result:       RFQResult from open (if RFQ mode)
        close_rfq_result: RFQResult from close (if RFQ mode)
        metadata:         Arbitrary strategy-provided context
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    strategy_id: Optional[str] = None
    state: TradeState = TradeState.PENDING_OPEN
    open_legs: List[TradeLeg] = field(default_factory=list)
    close_legs: List[TradeLeg] = field(default_factory=list)
    exit_conditions: List[ExitCondition] = field(default_factory=list)
    execution_mode: Optional[str] = None  # "limit", "rfq", "smart", or None (auto-route)
    rfq_action: str = "buy"             # "buy" or "sell" — for the open
    smart_config: Optional[SmartExecConfig] = None  # Config for "smart" mode
    created_at: float = field(default_factory=time.time)
    opened_at: Optional[float] = None
    closed_at: Optional[float] = None
    error: Optional[str] = None
    rfq_result: Optional[Any] = None
    close_rfq_result: Optional[Any] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # -- Helpers --------------------------------------------------------------

    @property
    def symbols(self) -> List[str]:
        return [leg.symbol for leg in self.open_legs]

    @property
    def age_seconds(self) -> float:
        return time.time() - self.created_at

    @property
    def hold_seconds(self) -> Optional[float]:
        """Time since all legs opened (None if not yet open)."""
        if self.opened_at is None:
            return None
        return time.time() - self.opened_at

    def _our_share(self, leg: "TradeLeg", pos: PositionSnapshot) -> float:
        """
        Fraction of the exchange position that belongs to this lifecycle.

        The exchange aggregates all holdings in the same contract into one
        position.  If we hold 0.5 and the total position is 1.0, our share
        is 0.5.  We clamp to [0, 1] as a safety measure.
        """
        if pos.qty == 0:
            return 0.0
        our_qty = leg.filled_qty if leg.filled_qty > 0 else leg.qty
        return min(our_qty / pos.qty, 1.0)

    def structure_pnl(self, account: AccountSnapshot) -> float:
        """Unrealised PnL for THIS lifecycle's legs only (pro-rated)."""
        total = 0.0
        for leg in self.open_legs:
            pos = account.get_position(leg.symbol)
            if pos:
                total += pos.unrealized_pnl * self._our_share(leg, pos)
        return total

    def structure_delta(self, account: AccountSnapshot) -> float:
        """Delta for THIS lifecycle's legs only (pro-rated)."""
        total = 0.0
        for leg in self.open_legs:
            pos = account.get_position(leg.symbol)
            if pos:
                total += pos.delta * self._our_share(leg, pos)
        return total

    def structure_greeks(self, account: AccountSnapshot) -> Dict[str, float]:
        """Aggregated Greeks for THIS lifecycle's legs only (pro-rated)."""
        d = g = t = v = 0.0
        for leg in self.open_legs:
            pos = account.get_position(leg.symbol)
            if pos:
                share = self._our_share(leg, pos)
                d += pos.delta * share
                g += pos.gamma * share
                t += pos.theta * share
                v += pos.vega * share
        return {"delta": d, "gamma": g, "theta": t, "vega": v}

    def total_entry_cost(self) -> float:
        """Sum of fill_price * qty across all open legs (signed by side)."""
        total = 0.0
        for leg in self.open_legs:
            if leg.fill_price is not None:
                sign = 1 if leg.side == 1 else -1  # buy = debit, sell = credit
                total += sign * leg.fill_price * leg.filled_qty
        return total

    def summary(self, account: Optional[AccountSnapshot] = None) -> str:
        legs_str = ", ".join(
            f"{l.side_label} {l.qty}x {l.symbol}" for l in self.open_legs
        )
        prefix = f"[{self.id}]"
        if self.strategy_id:
            prefix += f" ({self.strategy_id})"
        s = f"{prefix} {self.state.value} | {legs_str}"
        if account and self.state == TradeState.OPEN:
            pnl = self.structure_pnl(account)
            greeks = self.structure_greeks(account)
            s += f" | PnL={pnl:+.4f} Δ={greeks['delta']:+.4f}"
        return s


# =============================================================================
# Exit Condition Factories
# =============================================================================

def profit_target(pct: float) -> ExitCondition:
    """
    Close when structure PnL exceeds +pct% of entry cost.

    Example: profit_target(50) closes when profit >= 50% of premium received.
    """
    def _check(account: AccountSnapshot, trade: TradeLifecycle) -> bool:
        entry = trade.total_entry_cost()
        if entry == 0:
            return False
        pnl = trade.structure_pnl(account)
        # For credit trades (entry < 0), profit = -pnl > 0
        # For debit trades (entry > 0), profit = pnl > 0
        ratio = (pnl / abs(entry)) * 100 if entry != 0 else 0
        triggered = ratio >= pct
        if triggered:
            logger.info(f"[{trade.id}] profit_target({pct}%) triggered: PnL ratio={ratio:.1f}%")
        return triggered
    _check.__name__ = f"profit_target({pct}%)"
    return _check


def max_loss(pct: float) -> ExitCondition:
    """
    Close when structure loss exceeds pct% of entry cost.

    Example: max_loss(100) closes when loss >= 100% of premium received.
    """
    def _check(account: AccountSnapshot, trade: TradeLifecycle) -> bool:
        entry = trade.total_entry_cost()
        if entry == 0:
            return False
        pnl = trade.structure_pnl(account)
        ratio = (pnl / abs(entry)) * 100 if entry != 0 else 0
        triggered = ratio <= -pct
        if triggered:
            logger.info(f"[{trade.id}] max_loss({pct}%) triggered: PnL ratio={ratio:.1f}%")
        return triggered
    _check.__name__ = f"max_loss({pct}%)"
    return _check


def max_hold_hours(hours: float) -> ExitCondition:
    """Close when position has been open longer than N hours."""
    def _check(account: AccountSnapshot, trade: TradeLifecycle) -> bool:
        hold = trade.hold_seconds
        if hold is None:
            return False
        triggered = hold >= hours * 3600
        if triggered:
            logger.info(f"[{trade.id}] max_hold_hours({hours}h) triggered: held {hold/3600:.1f}h")
        return triggered
    _check.__name__ = f"max_hold_hours({hours}h)"
    return _check


def account_delta_limit(threshold: float) -> ExitCondition:
    """Close when account-wide absolute delta exceeds threshold."""
    def _check(account: AccountSnapshot, trade: TradeLifecycle) -> bool:
        triggered = abs(account.net_delta) > threshold
        if triggered:
            logger.info(
                f"[{trade.id}] account_delta_limit({threshold}) triggered: "
                f"account delta={account.net_delta:+.4f}"
            )
        return triggered
    _check.__name__ = f"account_delta_limit({threshold})"
    return _check


def structure_delta_limit(threshold: float) -> ExitCondition:
    """Close when this trade's absolute delta exceeds threshold."""
    def _check(account: AccountSnapshot, trade: TradeLifecycle) -> bool:
        d = trade.structure_delta(account)
        triggered = abs(d) > threshold
        if triggered:
            logger.info(
                f"[{trade.id}] structure_delta_limit({threshold}) triggered: "
                f"structure delta={d:+.4f}"
            )
        return triggered
    _check.__name__ = f"structure_delta_limit({threshold})"
    return _check


def leg_greek_limit(leg_index: int, greek: str, op: str, value: float) -> ExitCondition:
    """
    Close when a specific leg's Greek crosses a threshold.

    Args:
        leg_index: Index into open_legs (0 = first leg)
        greek: "delta", "gamma", "theta", or "vega"
        op: ">" or "<"
        value: Threshold value

    Example: leg_greek_limit(0, "theta", "<", -5.0)
             → close when first leg's theta drops below -5
    """
    def _check(account: AccountSnapshot, trade: TradeLifecycle) -> bool:
        if leg_index >= len(trade.open_legs):
            return False
        leg = trade.open_legs[leg_index]
        pos = account.get_position(leg.symbol)
        if pos is None:
            return False
        actual = getattr(pos, greek, 0.0)
        if op == ">":
            triggered = actual > value
        elif op == "<":
            triggered = actual < value
        else:
            return False
        if triggered:
            logger.info(
                f"[{trade.id}] leg_greek_limit(leg[{leg_index}].{greek} {op} {value}) "
                f"triggered: actual={actual:+.6f}"
            )
        return triggered
    _check.__name__ = f"leg[{leg_index}].{greek}{op}{value}"
    return _check


# =============================================================================
# Lifecycle Manager
# =============================================================================

class LifecycleManager:
    """
    Orchestrates one or more TradeLifecycles through their state machines.

    Usage:
        manager = LifecycleManager()

        # Hook into PositionMonitor so tick() runs on every snapshot
        position_monitor.on_update(manager.tick)

        # Create a trade
        trade = manager.create(
            legs=[
                TradeLeg(symbol="BTCUSD-20FEB26-70000-C", qty=0.01, side=1),
            ],
            exit_conditions=[profit_target(50), max_hold_hours(48)],
            execution_mode="limit",
        )

        # Open it (places orders)
        manager.open(trade.id)

        # From here, tick() handles everything:
        # - Detects fills  → moves to OPEN
        # - Evaluates exit conditions  → moves to PENDING_CLOSE
        # - Places close orders  → moves to CLOSING
        # - Detects close fills  → moves to CLOSED
    """

    def __init__(
        self,
        rfq_notional_threshold: float = 50000.0,
        smart_notional_threshold: float = 10000.0,
    ):
        """
        Initialize LifecycleManager with execution routing parameters.
        
        Args:
            rfq_notional_threshold: Use RFQ for multi-leg orders >= this notional (USD)
            smart_notional_threshold: Use smart dealing for multi-leg orders >= this notional
                and < rfq_notional_threshold. Below this, falls back to limit orders per leg.
        """
        self._trades: Dict[str, TradeLifecycle] = {}
        self._executor = TradeExecutor()
        self._rfq_executor = RFQExecutor()
        self._smart_executor = SmartOrderbookExecutor()
        
        # Execution routing thresholds
        self.rfq_notional_threshold = rfq_notional_threshold
        self.smart_notional_threshold = smart_notional_threshold

    @property
    def active_trades(self) -> List[TradeLifecycle]:
        """All trades that are not CLOSED or FAILED."""
        return [
            t for t in self._trades.values()
            if t.state not in (TradeState.CLOSED, TradeState.FAILED)
        ]

    @property
    def all_trades(self) -> List[TradeLifecycle]:
        return list(self._trades.values())

    def get(self, trade_id: str) -> Optional[TradeLifecycle]:
        return self._trades.get(trade_id)

    def get_trades_for_strategy(self, strategy_id: str) -> List[TradeLifecycle]:
        """All trades (any state) belonging to a strategy."""
        return [t for t in self._trades.values() if t.strategy_id == strategy_id]

    def active_trades_for_strategy(self, strategy_id: str) -> List[TradeLifecycle]:
        """Active (not CLOSED/FAILED) trades belonging to a strategy."""
        return [t for t in self.active_trades if t.strategy_id == strategy_id]

    # -------------------------------------------------------------------------
    # Create
    # -------------------------------------------------------------------------

    def create(
        self,
        legs: List[TradeLeg],
        exit_conditions: Optional[List[ExitCondition]] = None,
        execution_mode: Optional[str] = None,
        rfq_action: str = "buy",
        smart_config: Optional[SmartExecConfig] = None,
        strategy_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> TradeLifecycle:
        """
        Register a new trade intent.

        Args:
            legs: TradeLeg objects defining the structure to open
            exit_conditions: Callables that trigger a close when True
            execution_mode: "limit", "rfq", "smart", or None (auto-route).
                If None, execution mode will be auto-selected based on:
                - Single leg → "limit"
                - Multi-leg with notional >= rfq_threshold → "rfq"
                - Multi-leg with notional < rfq_threshold → "smart"
            rfq_action: "buy" or "sell" — passed to RFQExecutor
            smart_config: SmartExecConfig — optional, can be provided for "smart" mode
            metadata: Arbitrary context (strategy name, notes, etc.)

        Returns:
            TradeLifecycle in PENDING_OPEN state
        """
        trade = TradeLifecycle(
            open_legs=legs,
            strategy_id=strategy_id,
            exit_conditions=exit_conditions or [],
            execution_mode=execution_mode,
            rfq_action=rfq_action,
            smart_config=smart_config,
            metadata=metadata or {},
        )
        self._trades[trade.id] = trade
        logger.info(f"Trade {trade.id} created: {len(legs)} legs, mode={execution_mode or 'auto-route'}, strategy={strategy_id}")
        return trade

    # -------------------------------------------------------------------------
    # Execution Mode Routing
    # -------------------------------------------------------------------------

    def _determine_execution_mode(self, trade: TradeLifecycle) -> str:
        """
        Auto-determine execution mode based on trade characteristics.
        
        Called only if trade.execution_mode is None (auto-routing enabled).
        
        Logic:
          - Single leg → "limit"
          - Multi-leg, notional >= rfq_threshold → "rfq"
          - Multi-leg, smart_threshold <= notional < rfq_threshold → "smart"
          - Multi-leg, notional < smart_threshold → "limit" (fallback)
        
        Args:
            trade: TradeLifecycle to analyze
            
        Returns:
            Determined execution mode: "limit", "rfq", or "smart"
        """
        # Single leg always uses limit orders
        if len(trade.open_legs) == 1:
            logger.info(f"[{trade.id}] Single leg detected, using 'limit' mode")
            return "limit"
        
        # Multi-leg: calculate notional value
        notional = self._calculate_notional(trade.open_legs)
        logger.info(f"[{trade.id}] Multi-leg notional: ${notional:,.2f}")
        
        # Route based on notional thresholds
        if notional >= self.rfq_notional_threshold:
            logger.info(f"[{trade.id}] Notional >= ${self.rfq_notional_threshold:,.0f}, using 'rfq' mode")
            return "rfq"
        elif notional >= self.smart_notional_threshold:
            logger.info(f"[{trade.id}] ${self.smart_notional_threshold:,.0f} <= notional < ${self.rfq_notional_threshold:,.0f}, using 'smart' mode")
            return "smart"
        else:
            logger.info(f"[{trade.id}] Notional < ${self.smart_notional_threshold:,.0f}, using 'limit' mode (fallback)")
            return "limit"

    def _calculate_notional(self, legs: List[TradeLeg]) -> float:
        """
        Calculate total notional value of a multi-leg order.
        
        Notional = sum of (mark_price * qty) for each leg.
        Uses current orderbook mark prices.
        
        Args:
            legs: List of TradeLeg objects
            
        Returns:
            Total notional in USD
        """
        total_notional = 0.0
        
        for leg in legs:
            try:
                orderbook = get_option_orderbook(leg.symbol)
                if not orderbook:
                    logger.warning(f"Could not fetch orderbook for {leg.symbol}, using 0 notional")
                    continue
                
                mark_price = float(orderbook.get('mark', 0))
                if mark_price <= 0:
                    logger.warning(f"Invalid mark price for {leg.symbol}, skipping")
                    continue
                
                leg_notional = mark_price * leg.qty
                total_notional += leg_notional
                logger.debug(f"  {leg.symbol}: {leg.qty} @ ${mark_price} = ${leg_notional:,.2f}")
                
            except Exception as e:
                logger.warning(f"Error calculating notional for {leg.symbol}: {e}")
                continue
        
        return total_notional

    # -------------------------------------------------------------------------
    # Open
    # -------------------------------------------------------------------------

    def open(self, trade_id: str) -> bool:
        """
        Place orders to open a trade.

        If execution_mode is None, auto-determines best mode:
          - Single leg → "limit"
          - Multi-leg with large notional → "rfq"
          - Multi-leg with medium notional → "smart"
          - Multi-leg with small notional → "limit" (fallback)

        Then routes to the appropriate executor:
          - "limit": Individual limit orders via TradeExecutor
          - "rfq": Atomic multi-leg RFQ via RFQExecutor
          - "smart": Multi-leg smart orderbook with chunking via SmartOrderbookExecutor

        Returns True if orders were placed (not necessarily filled yet).
        """
        trade = self._trades.get(trade_id)
        if not trade:
            logger.error(f"Trade {trade_id} not found")
            return False
        if trade.state != TradeState.PENDING_OPEN:
            logger.error(f"Trade {trade_id} not in PENDING_OPEN (is {trade.state.value})")
            return False

        # Auto-determine execution mode if not explicitly set
        if trade.execution_mode is None:
            trade.execution_mode = self._determine_execution_mode(trade)

        logger.info(f"Opening trade {trade_id} via {trade.execution_mode}")

        if trade.execution_mode == "rfq":
            return self._open_rfq(trade)
        elif trade.execution_mode == "smart":
            return self._open_smart(trade)
        else:  # Default to "limit"
            return self._open_limit(trade)

    def _open_rfq(self, trade: TradeLifecycle) -> bool:
        """Open via RFQ — atomic multi-leg execution."""
        rfq_legs = [
            OptionLeg(
                instrument=leg.symbol,
                side="BUY" if leg.side == 1 else "SELL",
                qty=leg.qty,
            )
            for leg in trade.open_legs
        ]

        result: RFQResult = self._rfq_executor.execute(
            legs=rfq_legs,
            action=trade.rfq_action,
        )
        trade.rfq_result = result

        if result.success:
            # RFQ fills are atomic — all legs filled
            trade.state = TradeState.OPEN
            trade.opened_at = time.time()
            # Try to extract fill prices from RFQ result legs
            for i, leg in enumerate(trade.open_legs):
                leg.filled_qty = leg.qty
                if i < len(result.legs):
                    leg.fill_price = result.legs[i].get('price', 0.0)
            logger.info(f"Trade {trade.id} opened via RFQ (all legs filled)")
            return True
        else:
            trade.state = TradeState.FAILED
            trade.error = result.message
            logger.error(f"Trade {trade.id} RFQ failed: {result.message}")
            return False

    def _open_limit(self, trade: TradeLifecycle) -> bool:
        """Open via limit orders — one order per leg.

        Fetches the current orderbook for each leg to determine the limit
        price (best ask for buys, best bid for sells).

        Failure handling: if any leg fails to place, all previously placed
        orders are cancelled and the trade moves to FAILED.
        """
        trade.state = TradeState.OPENING

        for leg in trade.open_legs:
            try:
                price = self._get_orderbook_price(leg.symbol, leg.side)
                if price is None:
                    trade.error = f"No orderbook price for {leg.symbol} (side={leg.side_label})"
                    logger.error(f"Trade {trade.id}: {trade.error}")
                    self._cancel_placed_orders(trade.open_legs)
                    trade.state = TradeState.FAILED
                    return False

                result = self._executor.place_order(
                    symbol=leg.symbol,
                    qty=leg.qty,
                    side=leg.side,
                    order_type=1,  # limit
                    price=price,
                )
                if result:
                    leg.order_id = str(result.get('orderId', ''))
                    logger.info(f"Trade {trade.id}: placed order {leg.order_id} for {leg.side_label} {leg.qty}x {leg.symbol} @ ${price}")
                else:
                    trade.error = f"Failed to place order for {leg.symbol}"
                    logger.error(f"Trade {trade.id}: {trade.error}")
                    self._cancel_placed_orders(trade.open_legs)
                    trade.state = TradeState.FAILED
                    return False
            except Exception as e:
                trade.error = str(e)
                logger.error(f"Trade {trade.id}: exception placing order: {e}")
                self._cancel_placed_orders(trade.open_legs)
                trade.state = TradeState.FAILED
                return False

        logger.info(f"Trade {trade.id}: all {len(trade.open_legs)} open orders placed, awaiting fills")
        return True

    def _open_smart(self, trade: TradeLifecycle) -> bool:
        """Open via smart multi-leg orderbook execution with chunking."""
        if not trade.smart_config:
            trade.state = TradeState.FAILED
            trade.error = "smart_config required for 'smart' execution mode"
            logger.error(f"Trade {trade.id}: {trade.error}")
            return False

        trade.state = TradeState.OPENING
        
        try:
            logger.info(f"Trade {trade.id}: starting smart execution with {trade.smart_config.chunk_count} chunks")
            
            result = self._smart_executor.execute_smart_multi_leg(
                legs=trade.open_legs,
                config=trade.smart_config
            )
            
            if result.success:
                # Update legs with execution results
                for leg in trade.open_legs:
                    if leg.symbol in result.total_filled_qty:
                        leg.filled_qty = result.total_filled_qty[leg.symbol]
                
                trade.state = TradeState.OPEN
                trade.opened_at = time.time()
                logger.info(f"Trade {trade.id}: smart execution completed successfully")
                return True
            else:
                trade.state = TradeState.FAILED
                trade.error = result.message
                logger.error(f"Trade {trade.id}: smart execution failed: {result.message}")
                return False
                
        except Exception as e:
            trade.state = TradeState.FAILED
            trade.error = str(e)
            logger.error(f"Trade {trade.id}: exception in smart execution: {e}")
            return False

    # -------------------------------------------------------------------------
    # Close
    # -------------------------------------------------------------------------

    def close(self, trade_id: str) -> bool:
        """
        Place orders to close a trade.

        Generates close legs as the reverse of open legs and submits them.
        Returns True if close orders were placed.
        """
        trade = self._trades.get(trade_id)
        if not trade:
            logger.error(f"Trade {trade_id} not found")
            return False
        if trade.state not in (TradeState.OPEN, TradeState.PENDING_CLOSE):
            logger.error(f"Trade {trade_id} not closeable (is {trade.state.value})")
            return False

        logger.info(f"Closing trade {trade_id} via {trade.execution_mode}")

        # Build close legs (reverse of open)
        trade.close_legs = [
            TradeLeg(
                symbol=leg.symbol,
                qty=leg.filled_qty if leg.filled_qty > 0 else leg.qty,
                side=leg.close_side,
            )
            for leg in trade.open_legs
        ]

        if trade.execution_mode == "rfq":
            return self._close_rfq(trade)
        else:
            return self._close_limit(trade)

    def _close_rfq(self, trade: TradeLifecycle) -> bool:
        """Close via RFQ — atomic multi-leg execution.
        
        RFQs must always be submitted with legs as BUY (Coincall requirement).
        We use the SAME legs as the open, and reverse the action instead:
        if we bought the structure to open, we sell it to close.
        """
        # Use the ORIGINAL open legs (always BUY) — Coincall requires this
        rfq_legs = [
            OptionLeg(
                instrument=leg.symbol,
                side="BUY" if leg.side == 1 else "SELL",
                qty=leg.filled_qty if leg.filled_qty > 0 else leg.qty,
            )
            for leg in trade.open_legs
        ]

        # Reverse the action: if we bought to open, we sell to close
        close_action = "sell" if trade.rfq_action == "buy" else "buy"

        result: RFQResult = self._rfq_executor.execute(
            legs=rfq_legs,
            action=close_action,
        )
        trade.close_rfq_result = result

        if result.success:
            trade.state = TradeState.CLOSED
            trade.closed_at = time.time()
            for i, leg in enumerate(trade.close_legs):
                leg.filled_qty = leg.qty
                if i < len(result.legs):
                    leg.fill_price = result.legs[i].get('price', 0.0)
            logger.info(f"Trade {trade.id} closed via RFQ")
            return True
        else:
            # RFQ close failed — remain in PENDING_CLOSE so next tick retries
            trade.state = TradeState.PENDING_CLOSE
            logger.error(f"Trade {trade.id} RFQ close failed: {result.message}, will retry")
            return False

    def _close_limit(self, trade: TradeLifecycle) -> bool:
        """Close via limit orders — one order per leg.

        Fetches the current orderbook for each leg to determine the limit
        price (best ask for buys, best bid for sells).

        Failure handling: if any close order fails to place, all previously
        placed close orders are cancelled and the trade reverts to
        PENDING_CLOSE so the next tick retries the entire close atomically.
        """
        trade.state = TradeState.CLOSING

        for leg in trade.close_legs:
            try:
                price = self._get_orderbook_price(leg.symbol, leg.side)
                if price is None:
                    logger.error(
                        f"Trade {trade.id}: no orderbook price for close leg "
                        f"{leg.symbol} (side={leg.side_label}), will retry"
                    )
                    self._cancel_placed_orders(trade.close_legs)
                    trade.state = TradeState.PENDING_CLOSE
                    return False

                result = self._executor.place_order(
                    symbol=leg.symbol,
                    qty=leg.qty,
                    side=leg.side,
                    order_type=1,
                    price=price,
                )
                if result:
                    leg.order_id = str(result.get('orderId', ''))
                    logger.info(
                        f"Trade {trade.id}: placed close order {leg.order_id} "
                        f"for {leg.side_label} {leg.qty}x {leg.symbol} @ ${price}"
                    )
                else:
                    logger.error(f"Trade {trade.id}: failed to place close order for {leg.symbol}")
                    self._cancel_placed_orders(trade.close_legs)
                    trade.state = TradeState.PENDING_CLOSE
                    return False
            except Exception as e:
                logger.error(f"Trade {trade.id}: exception placing close order: {e}")
                self._cancel_placed_orders(trade.close_legs)
                trade.state = TradeState.PENDING_CLOSE
                return False

        logger.info(f"Trade {trade.id}: all close orders placed, awaiting fills")
        return True

    def _cancel_placed_orders(self, legs: List[TradeLeg]) -> None:
        """Cancel any orders already placed for the given legs (cleanup on failure)."""
        for leg in legs:
            if leg.order_id and not leg.is_filled:
                try:
                    self._executor.cancel_order(leg.order_id)
                    logger.info(f"Cancelled orphaned order {leg.order_id} for {leg.symbol}")
                except Exception as e:
                    logger.warning(f"Failed to cancel orphaned order {leg.order_id}: {e}")

    def _get_orderbook_price(self, symbol: str, side: int) -> Optional[float]:
        """Fetch best bid or ask from orderbook.

        The orderbook endpoint returns::

            {"bids": [{"price": "50", "size": "18.18"}, ...],
             "asks": [{"price": "95", "size": "18.18"}, ...]}

        Returns:
            The best ask price if side==1 (buy), best bid if side==2 (sell),
            or None if unavailable.
        """
        try:
            ob = get_option_orderbook(symbol)
            if not ob:
                return None

            # Orderbook endpoint returns nested bids/asks arrays
            if side == 1 and ob.get('asks'):
                return float(ob['asks'][0]['price'])
            elif side == 2 and ob.get('bids'):
                return float(ob['bids'][0]['price'])

            return None
        except Exception as e:
            logger.error(f"Error fetching orderbook price for {symbol}: {e}")
            return None

    # -------------------------------------------------------------------------
    # Fill Checking
    # -------------------------------------------------------------------------

    def _check_open_fills(self, trade: TradeLifecycle) -> None:
        """Poll order status for open legs. Transition to OPEN when all filled."""
        all_filled = True
        for leg in trade.open_legs:
            if leg.is_filled:
                continue
            if not leg.order_id:
                all_filled = False
                continue

            try:
                info = self._executor.get_order_status(leg.order_id)
                if info:
                    executed = float(info.get('fillQty', 0))
                    if executed > leg.filled_qty:
                        leg.filled_qty = executed
                        leg.fill_price = float(info.get('avgPrice', 0)) or leg.fill_price
                        logger.info(
                            f"Trade {trade.id}: {leg.symbol} filled "
                            f"{leg.filled_qty}/{leg.qty} @ {leg.fill_price}"
                        )

                    state_code = info.get('state')
                    # State 1 = FILLED, 3 = CANCELED (Coincall option order states)
                    if state_code == 3 and not leg.is_filled:
                        logger.warning(
                            f"Trade {trade.id}: order {leg.order_id} was cancelled, "
                            f"filled {leg.filled_qty}/{leg.qty}"
                        )

                if not leg.is_filled:
                    all_filled = False
            except Exception as e:
                logger.error(f"Trade {trade.id}: error checking order {leg.order_id}: {e}")
                all_filled = False

        if all_filled:
            trade.state = TradeState.OPEN
            trade.opened_at = time.time()
            logger.info(f"Trade {trade.id}: all open legs filled → OPEN")

    def _check_close_fills(self, trade: TradeLifecycle) -> None:
        """Poll order status for close legs. Transition to CLOSED when all filled."""
        all_filled = True
        for leg in trade.close_legs:
            if leg.is_filled:
                continue
            if not leg.order_id:
                all_filled = False
                continue

            try:
                info = self._executor.get_order_status(leg.order_id)
                if info:
                    executed = float(info.get('fillQty', 0))
                    if executed > leg.filled_qty:
                        leg.filled_qty = executed
                        leg.fill_price = float(info.get('avgPrice', 0)) or leg.fill_price
                        logger.info(
                            f"Trade {trade.id}: close {leg.symbol} filled "
                            f"{leg.filled_qty}/{leg.qty} @ {leg.fill_price}"
                        )

                if not leg.is_filled:
                    all_filled = False
            except Exception as e:
                logger.error(f"Trade {trade.id}: error checking close order {leg.order_id}: {e}")
                all_filled = False

        if all_filled:
            trade.state = TradeState.CLOSED
            trade.closed_at = time.time()
            logger.info(f"Trade {trade.id}: all close legs filled → CLOSED")

    # -------------------------------------------------------------------------
    # Exit Evaluation
    # -------------------------------------------------------------------------

    def _evaluate_exits(self, trade: TradeLifecycle, account: AccountSnapshot) -> None:
        """Check exit conditions for an OPEN trade. Any True → PENDING_CLOSE."""
        for cond in trade.exit_conditions:
            try:
                if cond(account, trade):
                    cond_name = getattr(cond, '__name__', repr(cond))
                    logger.info(
                        f"Trade {trade.id}: exit condition '{cond_name}' triggered → PENDING_CLOSE"
                    )
                    trade.state = TradeState.PENDING_CLOSE
                    return
            except Exception as e:
                logger.error(f"Trade {trade.id}: error evaluating exit condition: {e}")

    # -------------------------------------------------------------------------
    # Tick — the main heartbeat
    # -------------------------------------------------------------------------

    def tick(self, account: AccountSnapshot) -> None:
        """
        Advance all active trades one step through the state machine.

        Designed to be called as a PositionMonitor callback:
            position_monitor.on_update(manager.tick)

        Each call:
          - OPENING       → check fills → maybe OPEN
          - OPEN          → evaluate exits → maybe PENDING_CLOSE
          - PENDING_CLOSE → place close orders → CLOSING
          - CLOSING       → check close fills → maybe CLOSED
        """
        for trade in self.active_trades:
            try:
                if trade.state == TradeState.OPENING:
                    self._check_open_fills(trade)

                elif trade.state == TradeState.OPEN:
                    self._evaluate_exits(trade, account)

                elif trade.state == TradeState.PENDING_CLOSE:
                    self.close(trade.id)

                elif trade.state == TradeState.CLOSING:
                    self._check_close_fills(trade)

            except Exception as e:
                logger.error(f"Trade {trade.id}: tick error in state {trade.state.value}: {e}")

        # Persist trade state snapshot after processing
        if self._trades:
            self._persist_all_trades()

    # -------------------------------------------------------------------------
    # Manual Controls
    # -------------------------------------------------------------------------

    def force_close(self, trade_id: str) -> bool:
        """
        Force a trade into PENDING_CLOSE regardless of exit conditions.
        Useful for manual intervention or emergency close.
        """
        trade = self._trades.get(trade_id)
        if not trade:
            return False
        if trade.state == TradeState.OPEN:
            logger.info(f"Trade {trade.id}: forced close by user")
            trade.state = TradeState.PENDING_CLOSE
            return True
        logger.warning(f"Trade {trade.id}: cannot force close in state {trade.state.value}")
        return False

    def cancel(self, trade_id: str) -> bool:
        """
        Cancel a trade that hasn't fully opened yet.
        Cancels any outstanding orders and marks as FAILED.
        """
        trade = self._trades.get(trade_id)
        if not trade:
            return False
        if trade.state in (TradeState.PENDING_OPEN, TradeState.OPENING):
            for leg in trade.open_legs:
                if leg.order_id and not leg.is_filled:
                    try:
                        self._executor.cancel_order(leg.order_id)
                    except Exception as e:
                        logger.warning(f"Failed to cancel order {leg.order_id}: {e}")
            trade.state = TradeState.FAILED
            trade.error = "Cancelled by user"
            logger.info(f"Trade {trade.id}: cancelled")
            return True
        logger.warning(f"Trade {trade.id}: cannot cancel in state {trade.state.value}")
        return False

    # -------------------------------------------------------------------------
    # Persistence
    # -------------------------------------------------------------------------

    def _persist_all_trades(self) -> None:
        """Dump all trade states to JSON for inspection and crash recovery."""
        try:
            os.makedirs("logs", exist_ok=True)
            trades_data = []
            for trade in self._trades.values():
                trades_data.append({
                    "id": trade.id,
                    "strategy_id": trade.strategy_id,
                    "state": trade.state.value,
                    "symbols": trade.symbols,
                    "execution_mode": trade.execution_mode,
                    "created_at": trade.created_at,
                    "opened_at": trade.opened_at,
                    "closed_at": trade.closed_at,
                    "error": trade.error,
                    "open_legs": [
                        {"symbol": l.symbol, "qty": l.qty, "side": l.side,
                         "filled_qty": l.filled_qty, "fill_price": l.fill_price,
                         "order_id": l.order_id}
                        for l in trade.open_legs
                    ],
                    "close_legs": [
                        {"symbol": l.symbol, "qty": l.qty, "side": l.side,
                         "filled_qty": l.filled_qty, "fill_price": l.fill_price,
                         "order_id": l.order_id}
                        for l in trade.close_legs
                    ],
                })
            with open("logs/trades_snapshot.json", "w") as f:
                json.dump({"timestamp": time.time(), "trades": trades_data}, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to persist trade snapshot: {e}")

    def status_report(self, account: Optional[AccountSnapshot] = None) -> str:
        """Human-readable status of all trades."""
        if not self._trades:
            return "No trades."
        lines = [f"{'ID':<14} {'State':<15} {'Legs':>4}  Description"]
        lines.append("-" * 70)
        for trade in self._trades.values():
            lines.append(trade.summary(account))
        return "\n".join(lines)


# =============================================================================
# Global Instance
# =============================================================================

lifecycle_manager = LifecycleManager()
