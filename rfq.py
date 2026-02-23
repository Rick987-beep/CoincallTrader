#!/usr/bin/env python3
"""
RFQ (Request for Quote) Execution Module

Handles multi-leg option structure execution via Coincall's block trade RFQ system.
As a Taker, this module:
  1. Creates RFQ requests with multiple option legs
  2. Monitors incoming quotes from market makers
  3. Compares quotes against orderbook prices
  4. Accepts the best quote or cancels if no suitable offer

Key Concepts:
  - Each leg specifies its own side ("BUY" or "SELL"); spreads use both
  - Market makers respond with two-way quotes (both BUY and SELL sides)
  - Quote 'side' field indicates the MARKET MAKER's action, not ours:
      MM "SELL" = they sell to us = WE BUY = positive cost (we pay)
      MM "BUY" = they buy from us = WE SELL = negative cost (we receive)
  - Minimum notional: $50,000 (sum of strike values)
  - Accept/Cancel endpoints require form-urlencoded data

Usage:
    from rfq import RFQExecutor, OptionLeg

    # Open a long strangle (BUY both legs)
    legs = [
        OptionLeg(symbol='BTCUSD-28FEB26-100000-C', qty=0.5, side='BUY'),
        OptionLeg(symbol='BTCUSD-28FEB26-90000-P',  qty=0.5, side='BUY'),
    ]
    rfq = RFQExecutor()
    result = rfq.execute(legs, action='buy', timeout_seconds=60)

    # Close the position (SELL both legs)
    result = rfq.execute(legs, action='sell', timeout_seconds=60)
"""

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any

from config import BASE_URL, API_KEY, API_SECRET
from auth import CoincallAuth
from market_data import get_option_orderbook

logger = logging.getLogger(__name__)


# =============================================================================
# Data Classes
# =============================================================================

@dataclass
class OptionLeg:
    """
    Represents a single leg in an RFQ structure.
    
    Attributes:
        instrument: Full option name (e.g., "BTCUSD-28FEB26-100000-C")
        side: Trade direction - "BUY" or "SELL"
        qty: Quantity for this leg
    """
    instrument: str
    side: str  # "BUY" or "SELL"
    qty: float
    
    def __post_init__(self):
        """Validate leg parameters"""
        self.side = self.side.upper()
        if self.side not in ("BUY", "SELL"):
            raise ValueError(f"Invalid side '{self.side}', must be 'BUY' or 'SELL'")
        if self.qty <= 0:
            raise ValueError(f"Quantity must be positive, got {self.qty}")
    
    def to_api_format(self) -> Dict[str, str]:
        """Convert to Coincall API format"""
        return {
            "instrumentName": self.instrument,
            "side": self.side,
            "qty": str(self.qty)
        }


class RFQState(Enum):
    """RFQ lifecycle states from Coincall API"""
    PENDING = "PENDING"      # Not yet submitted
    ACTIVE = "ACTIVE"        # Submitted, waiting for quotes
    FILLED = "FILLED"        # Quote accepted and executed
    CANCELLED = "CANCELLED"  # Cancelled by user
    EXPIRED = "EXPIRED"      # Timed out without execution
    TRADED_AWAY = "TRADED_AWAY"  # Another quote was accepted (maker perspective)


@dataclass
class RFQQuote:
    """
    Represents a quote received from a market maker.
    
    Attributes:
        quote_id: Unique quote identifier
        request_id: Associated RFQ request ID
        state: Quote state (OPEN, CANCELLED, FILLED)
        legs: List of leg prices from the maker
        create_time: Quote creation timestamp (ms)
        expiry_time: Quote expiration timestamp (ms)
        total_cost: Calculated total cost across all legs
    """
    quote_id: str
    request_id: str
    state: str
    legs: List[Dict[str, Any]]
    create_time: int
    expiry_time: int
    total_cost: float = 0.0
    
    @classmethod
    def from_api_response(cls, data: Dict[str, Any]) -> "RFQQuote":
        """Create RFQQuote from API response data"""
        legs = data.get("legs", [])
        
        # Calculate total cost: sum of (price * quantity) for each leg
        # The quote's 'side' field indicates the MARKET MAKER's action:
        # - MM "BUY" = MM buys from us = we SELL = we RECEIVE money (negative cost)
        # - MM "SELL" = MM sells to us = we BUY = we PAY money (positive cost)
        total_cost = 0.0
        for leg in legs:
            price = float(leg.get("price", 0))
            qty = float(leg.get("quantity", leg.get("qty", 0)))
            mm_side = leg.get("side", "").upper()  # Market maker's side
            
            if mm_side == "SELL":
                # MM sells to us = we BUY = we pay
                total_cost += price * qty
            else:
                # MM buys from us = we SELL = we receive
                total_cost -= price * qty
        
        return cls(
            quote_id=str(data.get("quoteId", "")),
            request_id=str(data.get("requestId", "")),
            state=data.get("state", "OPEN"),
            legs=legs,
            create_time=data.get("createTime", 0),
            expiry_time=data.get("expiryTime", 0),
            total_cost=total_cost
        )
    
    @property
    def is_we_buy(self) -> bool:
        """Returns True if accepting this quote means WE BUY (MM sells to us)"""
        if not self.legs:
            return False
        return self.legs[0].get("side", "").upper() == "SELL"
    
    @property
    def is_we_sell(self) -> bool:
        """Returns True if accepting this quote means WE SELL (MM buys from us)"""
        if not self.legs:
            return False
        return self.legs[0].get("side", "").upper() == "BUY"


@dataclass
class RFQResult:
    """
    Result of an RFQ execution attempt.
    
    Attributes:
        success: Whether the RFQ was successfully filled
        request_id: The RFQ request ID
        quote_id: The accepted quote ID (if filled)
        state: Final state of the RFQ
        legs: Executed leg details (if filled)
        total_cost: Total cost of the executed trade
        orderbook_cost: What the trade would have cost on screen
        improvement_pct: Percentage improvement vs orderbook
        message: Human-readable status message
    """
    success: bool
    request_id: str
    quote_id: Optional[str] = None
    state: RFQState = RFQState.PENDING
    legs: List[Dict[str, Any]] = field(default_factory=list)
    total_cost: float = 0.0
    orderbook_cost: float = 0.0
    improvement_pct: float = 0.0
    message: str = ""


# =============================================================================
# RFQ Executor
# =============================================================================

class RFQExecutor:
    """
    Handles RFQ creation, monitoring, and execution for multi-leg structures.
    
    This class manages the complete RFQ lifecycle:
      1. Create RFQ with multiple option legs
      2. Poll for incoming quotes from market makers
      3. Compare quotes against orderbook prices
      4. Accept best quote or cancel if none meet criteria
    """
    
    def __init__(self):
        """Initialize RFQ executor with authenticated API client"""
        self.auth = CoincallAuth(API_KEY, API_SECRET, BASE_URL)
    
    # -------------------------------------------------------------------------
    # Core RFQ Operations
    # -------------------------------------------------------------------------
    
    def create_rfq(self, legs: List[OptionLeg]) -> Optional[Dict[str, Any]]:
        """
        Create a new RFQ request.
        
        Args:
            legs: List of OptionLeg objects defining the structure
            
        Returns:
            API response with requestId, expiryTime, state, etc.
            None if creation failed.
        """
        if len(legs) < 2:
            logger.warning("RFQ requires at least 2 legs for block trade")
            # Note: Coincall may allow single-leg RFQs for large sizes
        
        payload = {
            "legs": [leg.to_api_format() for leg in legs]
        }
        
        try:
            response = self.auth.post(
                '/open/option/blocktrade/request/create/v1',
                payload
            )
            
            if self.auth.is_successful(response):
                data = response.get('data', {})
                request_id = data.get('requestId')
                state = data.get('state')
                expiry = data.get('expiryTime')
                
                logger.info(
                    f"RFQ created: requestId={request_id}, state={state}, "
                    f"expires={time.strftime('%H:%M:%S', time.localtime(expiry/1000)) if expiry else 'N/A'}"
                )
                return data
            else:
                logger.error(f"RFQ creation failed: {response.get('msg')}")
                return None
                
        except Exception as e:
            logger.error(f"Exception creating RFQ: {e}")
            return None
    
    def get_quotes(self, request_id: str) -> List[RFQQuote]:
        """
        Get all quotes received for an RFQ request.
        
        Args:
            request_id: The RFQ request ID
            
        Returns:
            List of RFQQuote objects
        """
        try:
            response = self.auth.get(
                f'/open/option/blocktrade/request/getQuotesReceived/v1?requestId={request_id}'
            )
            
            if self.auth.is_successful(response):
                quotes_data = response.get('data', [])
                quotes = [RFQQuote.from_api_response(q) for q in quotes_data]
                
                if quotes:
                    logger.info(f"Received {len(quotes)} quote(s) for RFQ {request_id}")
                    for q in quotes:
                        logger.debug(f"  Quote {q.quote_id}: cost={q.total_cost:.2f}, state={q.state}")
                
                return quotes
            else:
                logger.debug(f"No quotes yet for RFQ {request_id}: {response.get('msg')}")
                return []
                
        except Exception as e:
            logger.error(f"Exception getting quotes for RFQ {request_id}: {e}")
            return []
    
    def accept_quote(self, request_id: str, quote_id: str) -> Optional[Dict[str, Any]]:
        """
        Accept a quote and execute the block trade.
        
        Args:
            request_id: The RFQ request ID
            quote_id: The quote ID to accept
            
        Returns:
            Execution response with trade details, or None if failed
        """
        try:
            # Use form-urlencoded data for accept endpoint
            response = self.auth.post(
                '/open/option/blocktrade/request/accept/v1',
                {
                    'requestId': str(request_id),
                    'quoteId': str(quote_id)
                },
                use_form_data=True
            )
            
            if self.auth.is_successful(response):
                data = response.get('data', {})
                logger.info(f"Quote {quote_id} accepted for RFQ {request_id}")
                return data
            else:
                logger.error(f"Failed to accept quote {quote_id}: {response.get('msg')}")
                return None
                
        except Exception as e:
            logger.error(f"Exception accepting quote {quote_id}: {e}")
            return None
    
    def cancel_rfq(self, request_id: str) -> bool:
        """
        Cancel an active RFQ request.
        
        Args:
            request_id: The RFQ request ID to cancel
            
        Returns:
            True if cancelled successfully
        """
        try:
            # Use form-urlencoded data for cancel endpoint
            response = self.auth.post(
                '/open/option/blocktrade/request/cancel/v1',
                {'requestId': str(request_id)},
                use_form_data=True
            )
            
            if self.auth.is_successful(response):
                logger.info(f"RFQ {request_id} cancelled")
                return True
            else:
                logger.error(f"Failed to cancel RFQ {request_id}: {response.get('msg')}")
                return False
                
        except Exception as e:
            logger.error(f"Exception cancelling RFQ {request_id}: {e}")
            return False
    
    def get_rfq_status(self, request_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the current status of an RFQ.
        
        Args:
            request_id: The RFQ request ID
            
        Returns:
            RFQ status data or None if not found
        """
        try:
            response = self.auth.get(
                f'/open/option/blocktrade/rfqList/v1?requestId={request_id}'
            )
            
            if self.auth.is_successful(response):
                data = response.get('data', {})
                rfq_list = data.get('rfqList', [])
                if rfq_list:
                    return rfq_list[0]
            return None
            
        except Exception as e:
            logger.error(f"Exception getting RFQ status: {e}")
            return None
    
    # -------------------------------------------------------------------------
    # Orderbook Comparison
    # -------------------------------------------------------------------------
    
    def get_orderbook_cost(self, legs: List[OptionLeg], action: str = "buy") -> Optional[float]:
        """
        Calculate the total cost to execute this structure on the orderbook.
        
        The `action` parameter determines what WE want to do with the
        structure.  Each leg's side defines the structure itself (e.g. a
        strangle is two BUY legs).  The combination of action + leg side
        determines which orderbook side to hit:
        
          action="buy"  + leg BUY  → we buy this leg  → pay the ask
          action="buy"  + leg SELL → we sell this leg → hit the bid
          action="sell" + leg BUY  → we sell this leg → hit the bid
          action="sell" + leg SELL → we buy this leg  → pay the ask
        
        Args:
            legs: List of OptionLeg objects defining the structure
            action: "buy" or "sell" — what we want to do with the structure
            
        Returns:
            Total cost (positive = net debit, negative = net credit)
            None if orderbook data unavailable for any leg
        """
        total_cost = 0.0
        want_to_buy = action.lower() == "buy"
        
        for leg in legs:
            try:
                orderbook = get_option_orderbook(leg.instrument)
                
                if not orderbook:
                    logger.warning(f"No orderbook data for {leg.instrument}")
                    return None
                
                # Determine effective direction for this leg
                # "Effectively buying" = (leg is BUY and we buy the structure)
                #                     OR (leg is SELL and we sell the structure)
                effectively_buying = (leg.side == "BUY") == want_to_buy
                
                if effectively_buying:
                    # We buy this leg → pay the ask
                    asks = orderbook.get('asks', [])
                    if not asks:
                        logger.warning(f"No asks for {leg.instrument}")
                        return None
                    price = float(asks[0]['price'])
                    total_cost += price * leg.qty
                else:
                    # We sell this leg → hit the bid
                    bids = orderbook.get('bids', [])
                    if not bids:
                        logger.warning(f"No bids for {leg.instrument}")
                        return None
                    price = float(bids[0]['price'])
                    total_cost -= price * leg.qty
                    
            except Exception as e:
                logger.error(f"Error getting orderbook for {leg.instrument}: {e}")
                return None
        
        return total_cost
    
    def calculate_improvement(
        self, 
        quote_cost: float, 
        orderbook_cost: float
    ) -> float:
        """
        Calculate percentage improvement of quote vs orderbook.
        
        Positive = quote is better than the book.
        Works for both paying (positive cost) and receiving (negative cost):
          - Paying  $110 vs book $115 → +4.3%  (we save money)
          - Receiving $85 vs book $80  → +6.25% (we get more)
        
        Args:
            quote_cost: Total cost from RFQ quote (positive=debit, negative=credit)
            orderbook_cost: Total cost on orderbook (same sign convention)
            
        Returns:
            Improvement percentage (positive = quote is better)
        """
        if orderbook_cost == 0:
            return 0.0
        
        # Unified formula: "how much better is the quote vs the book?"
        # Paying less (lower positive) or receiving more (lower negative)
        # both result in (book - quote) being positive → positive improvement.
        return (orderbook_cost - quote_cost) / abs(orderbook_cost) * 100
    
    # -------------------------------------------------------------------------
    # High-Level Execution
    # -------------------------------------------------------------------------
    
    def execute(
        self,
        legs: List[OptionLeg],
        action: str = "buy",
        timeout_seconds: int = 60,
        min_improvement_pct: float = -999.0,
        poll_interval_seconds: int = 3,
    ) -> RFQResult:
        """
        Execute a complete RFQ workflow.
        
        This method:
          1. Gets orderbook prices for comparison baseline
          2. Creates the RFQ with all legs
          3. Polls for incoming quotes from multiple MMs
          4. Sorts quotes by price (best first) and logs all of them
          5. Checks best quote against orderbook (min_improvement_pct gate)
          6. Accepts the best qualifying quote, or cancels if none
        
        Args:
            legs: List of OptionLeg objects defining the structure
            action: "buy" to buy the structure or "sell" to sell it
            timeout_seconds: Maximum time to wait for quotes (default: 60s)
            min_improvement_pct: Minimum improvement vs orderbook to accept.
                -999 = accept anything (default). 0 = must match book.
                Positive = must beat book by N%.
            poll_interval_seconds: How often to poll for quotes (default: 3s)
            
        Returns:
            RFQResult with execution details
            
        Note:
            - Each leg's side is passed to the API as-is (BUY or SELL)
            - Market makers respond with two-way quotes
            - The 'action' parameter determines which quote direction we accept
        """
        result = RFQResult(success=False, request_id="")
        want_to_buy = action.lower() == "buy"
        
        # Log the structure we're quoting
        action_str = "BUYING" if want_to_buy else "SELLING"
        logger.info(f"Starting RFQ execution: {action_str} {len(legs)} legs:")
        for leg in legs:
            logger.info(f"  {leg.qty} x {leg.instrument}")
        
        # Step 1: Get orderbook baseline (must use same action direction)
        orderbook_cost = self.get_orderbook_cost(legs, action=action)
        if orderbook_cost is not None:
            result.orderbook_cost = orderbook_cost
            logger.info(f"Orderbook cost baseline: {orderbook_cost:.2f}")
        else:
            logger.warning("Could not get orderbook baseline, proceeding without comparison")
        
        # Step 2: Create RFQ
        rfq_data = self.create_rfq(legs)
        if not rfq_data:
            result.message = "Failed to create RFQ"
            return result
        
        request_id = rfq_data.get('requestId')
        result.request_id = request_id
        result.state = RFQState.ACTIVE
        
        expiry_time = rfq_data.get('expiryTime', 0)
        rfq_timeout = min(timeout_seconds, (expiry_time - int(time.time() * 1000)) / 1000) if expiry_time else timeout_seconds
        
        logger.info(f"RFQ {request_id} active, waiting up to {rfq_timeout:.0f}s for quotes")
        
        # Step 3: Poll for quotes, sort, gate, and accept best
        start_time = time.time()
        accepted = False
        
        try:
            while time.time() - start_time < rfq_timeout and not accepted:
                quotes = self.get_quotes(request_id)
                
                # Filter to open quotes matching our direction, not expired
                now_ms = int(time.time() * 1000)
                valid_quotes = []
                for q in quotes:
                    if q.state != "OPEN":
                        continue
                    if want_to_buy and not q.is_we_buy:
                        continue
                    if not want_to_buy and not q.is_we_sell:
                        continue
                    if q.expiry_time and q.expiry_time < now_ms + 1000:
                        logger.debug(f"Skipping expired quote {q.quote_id}")
                        continue
                    valid_quotes.append(q)
                
                if valid_quotes:
                    # Sort: cheapest first (for buying) or most credit first (for selling)
                    # In both cases, lower total_cost is better
                    valid_quotes.sort(key=lambda q: q.total_cost)
                    
                    # Log all valid quotes
                    for i, q in enumerate(valid_quotes):
                        tag = "BEST" if i == 0 else f"#{i+1}"
                        action_tag = "WE BUY" if q.is_we_buy else "WE SELL"
                        ttl = (q.expiry_time - now_ms) / 1000
                        improvement = self.calculate_improvement(q.total_cost, orderbook_cost) if orderbook_cost is not None else 0
                        logger.info(
                            f"[{tag}] Quote {q.quote_id} ({action_tag}): "
                            f"cost=${q.total_cost:.2f}, "
                            f"vs book={improvement:+.1f}%, "
                            f"expires in {ttl:.0f}s"
                        )
                    
                    best = valid_quotes[0]
                    
                    # Gate: check improvement vs orderbook
                    if orderbook_cost is not None:
                        improvement = self.calculate_improvement(best.total_cost, orderbook_cost)
                        if improvement < min_improvement_pct:
                            logger.info(
                                f"Best quote {improvement:+.1f}% vs book "
                                f"(need {min_improvement_pct:+.1f}%), "
                                f"waiting for better quotes..."
                            )
                            # Don't accept yet, keep polling for better quotes
                            time.sleep(poll_interval_seconds)
                            continue
                    
                    # Try to accept the best quote (fall through to next best on failure)
                    for q in valid_quotes:
                        logger.info(
                            f"Accepting quote {q.quote_id}: "
                            f"{'paying' if q.total_cost > 0 else 'receiving'} "
                            f"${abs(q.total_cost):.2f}"
                        )
                        accept_response = self.accept_quote(request_id, q.quote_id)
                        
                        if accept_response:
                            imp = self.calculate_improvement(q.total_cost, orderbook_cost) if orderbook_cost is not None else 0
                            result.success = True
                            result.quote_id = q.quote_id
                            result.state = RFQState.FILLED
                            result.legs = accept_response.get('legs', q.legs)
                            result.total_cost = q.total_cost
                            result.improvement_pct = imp
                            if q.total_cost > 0:
                                result.message = f"{action_str} filled: paid ${q.total_cost:.2f} (vs book {imp:+.1f}%)"
                            else:
                                result.message = f"{action_str} filled: received ${abs(q.total_cost):.2f} (vs book {imp:+.1f}%)"
                            accepted = True
                            break
                        else:
                            logger.warning(f"Quote {q.quote_id} accept failed, trying next...")
                
                if accepted:
                    break
                
                # Wait before next poll
                time.sleep(poll_interval_seconds)

            if not accepted:
                result.message = result.message or f"No {action} quotes accepted within timeout"
                self.cancel_rfq(request_id)
                result.state = RFQState.CANCELLED
                
        except Exception as e:
            logger.error(f"Error during RFQ execution: {e}")
            result.message = f"Execution error: {e}"
            try:
                self.cancel_rfq(request_id)
            except:
                pass
            result.state = RFQState.CANCELLED
        
        # Log final result
        if result.success:
            logger.info(f"RFQ {request_id} completed successfully: {result.message}")
        else:
            logger.warning(f"RFQ {request_id} failed: {result.message}")
        
        return result
