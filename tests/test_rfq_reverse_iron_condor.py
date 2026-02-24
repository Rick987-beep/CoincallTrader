#!/usr/bin/env python3
"""
Reverse Iron Condor RFQ Test

Tests the leg selection and RFQ quoting for a reverse iron condor:
  - Inner legs: delta ±0.45 (1DTE)
  - Outer legs: $1,000 further away each
  - Mixed sides: BUY inner, SELL outer (net long position)

Monitors RFQ quotes for 30 seconds WITHOUT accepting them,
then cancels the RFQ.

Usage:
    python tests/test_rfq_reverse_iron_condor.py
"""

import logging
import sys
import time

from rfq import RFQExecutor, OptionLeg
from option_selection import select_option

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Configuration (matches reverse_iron_condor_live.py)
# ──────────────────────────────────────────────────────────────────────────────

QTY = 0.6                           # 0.6 BTC per leg
DTE = 1                             # 1 day to expiry
INNER_CALL_DELTA = 0.45
INNER_PUT_DELTA = -0.45
STRIKE_OFFSET = 1000                # Outer legs are $1,000 further OTM
MONITOR_SECONDS = 30                # Monitor for 30s without accepting
POLL_INTERVAL = 5                   # Check every 5 seconds


def resolve_reverse_iron_condor():
    """
    Resolve reverse iron condor strikes:
      1. Find inner call/put by delta (1DTE)
      2. Extract their strike prices
      3. Build outer strikes (offset by $1,000)
      4. Return all 4 symbols

    Structure:
      - BUY  inner put  (δ ≈ -0.45)
      - SELL outer put  (strike - $1,000)
      - BUY  inner call (δ ≈ +0.45)
      - SELL outer call (strike + $1,000)
    """
    logger.info(
        f"Resolving reverse iron condor: "
        f"call_delta={INNER_CALL_DELTA}, put_delta={INNER_PUT_DELTA}, "
        f"dte={DTE}, offset=${STRIKE_OFFSET}"
    )

    # Resolve inner call by delta (1DTE)
    inner_call_sym = select_option(
        expiry_criteria={"dte": DTE},
        strike_criteria={"type": "delta", "value": INNER_CALL_DELTA},
        option_type="C",
    )
    if not inner_call_sym:
        logger.error(f"Could not resolve inner call with delta {INNER_CALL_DELTA}")
        sys.exit(1)

    # Resolve inner put by delta (1DTE)
    inner_put_sym = select_option(
        expiry_criteria={"dte": DTE},
        strike_criteria={"type": "delta", "value": INNER_PUT_DELTA},
        option_type="P",
    )
    if not inner_put_sym:
        logger.error(f"Could not resolve inner put with delta {INNER_PUT_DELTA}")
        sys.exit(1)

    # Extract strikes from symbol names (e.g., BTCUSD-25FEB26-75000-C)
    parts_call = inner_call_sym.split("-")
    parts_put = inner_put_sym.split("-")

    try:
        inner_call_strike = int(parts_call[2])
        inner_put_strike = int(parts_put[2])
    except (IndexError, ValueError) as e:
        logger.error(f"Could not parse strikes from symbols: {e}")
        sys.exit(1)

    # Calculate outer strikes
    outer_call_strike = inner_call_strike + STRIKE_OFFSET
    outer_put_strike = inner_put_strike - STRIKE_OFFSET

    # Extract expiry token from symbol (e.g., 25FEB26)
    expiry_token = parts_call[1]
    prefix = f"BTCUSD-{expiry_token}"

    # Build all 4 symbols
    outer_call_sym = f"{prefix}-{outer_call_strike}-C"
    outer_put_sym = f"{prefix}-{outer_put_strike}-P"

    logger.info(
        f"Inner strikes resolved: "
        f"call={inner_call_strike} (from {inner_call_sym}), "
        f"put={inner_put_strike} (from {inner_put_sym})"
    )
    logger.info(
        f"Outer strikes calculated: "
        f"call={outer_call_strike}, put={outer_put_strike}"
    )

    return {
        "inner_call": (inner_call_sym, inner_call_strike),
        "outer_call": (outer_call_sym, outer_call_strike),
        "inner_put": (inner_put_sym, inner_put_strike),
        "outer_put": (outer_put_sym, outer_put_strike),
    }


def main():
    """Run the RFQ test without accepting quotes."""
    instruments = resolve_reverse_iron_condor()

    ic = instruments["inner_call"]
    oc = instruments["outer_call"]
    ip = instruments["inner_put"]
    op = instruments["outer_put"]

    logger.info("")
    logger.info("=" * 70)
    logger.info("REVERSE IRON CONDOR STRUCTURE")
    logger.info("=" * 70)
    logger.info(f"  BUY  {op[0]}  (outer put,  ${op[1]} strike)")
    logger.info(f"  SELL {ip[0]}  (inner put,  ${ip[1]} strike)")
    logger.info(f"  SELL {ic[0]}  (inner call, ${ic[1]} strike)")
    logger.info(f"  BUY  {oc[0]}  (outer call, ${oc[1]} strike)")
    logger.info("=" * 70)
    logger.info("")

    # Define legs for RFQ (order: outer put, inner put, inner call, outer call)
    legs = [
        OptionLeg(instrument=op[0], side="BUY",  qty=QTY),   # outer put — BUY
        OptionLeg(instrument=ip[0], side="SELL", qty=QTY),   # inner put — SELL
        OptionLeg(instrument=ic[0], side="SELL", qty=QTY),   # inner call — SELL
        OptionLeg(instrument=oc[0], side="BUY",  qty=QTY),   # outer call — BUY
    ]

    rfq = RFQExecutor()

    # ── Get orderbook baselines ──────────────────────────────────────────
    logger.info("Fetching orderbook baselines...")
    buy_book = rfq.get_orderbook_cost(legs, action="buy")
    sell_book = rfq.get_orderbook_cost(legs, action="sell")

    logger.info("")
    logger.info("=" * 70)
    logger.info("ORDERBOOK BASELINES")
    logger.info("=" * 70)
    if buy_book is not None:
        logger.info(
            f"BUY  (we pay to acquire structure):  ${buy_book:>10.2f}"
        )
    else:
        logger.info("BUY  (we pay to acquire structure):  N/A")

    if sell_book is not None:
        logger.info(
            f"SELL (we receive to sell structure): ${sell_book:>10.2f}"
        )
    else:
        logger.info("SELL (we receive to sell structure): N/A")
    logger.info("=" * 70)
    logger.info("")

    # ── Create RFQ ───────────────────────────────────────────────────────
    logger.info("Creating RFQ...")
    rfq_data = rfq.create_rfq(legs)
    if not rfq_data:
        logger.error("Failed to create RFQ — check notional minimum ($50K)")
        logger.error(
            f"Estimated notional: 0.6 BTC × 4 legs × ~$43,000 ≈ ~$103K "
            "(should be sufficient)"
        )
        sys.exit(1)

    request_id = rfq_data["requestId"]
    logger.info(f"RFQ created successfully: {request_id}")
    logger.info("")
    logger.info("=" * 70)
    logger.info(f"MONITORING FOR {MONITOR_SECONDS}s — NO QUOTES WILL BE ACCEPTED")
    logger.info("=" * 70)
    logger.info("")

    # ── Monitor loop (no acceptance) ──────────────────────────────────────
    start = time.time()

    try:
        while time.time() - start < MONITOR_SECONDS:
            elapsed = time.time() - start
            quotes = rfq.get_quotes(request_id)
            now_ms = int(time.time() * 1000)

            # Refresh orderbook
            buy_book_now = rfq.get_orderbook_cost(legs, action="buy")
            sell_book_now = rfq.get_orderbook_cost(legs, action="sell")

            # Separate and filter open quotes
            buy_quotes = [
                q for q in quotes
                if q.state == "OPEN"
                and q.is_we_buy
                and (not q.expiry_time or q.expiry_time > now_ms + 1000)
            ]
            sell_quotes = [
                q for q in quotes
                if q.state == "OPEN"
                and q.is_we_sell
                and (not q.expiry_time or q.expiry_time > now_ms + 1000)
            ]

            # Sort by best price
            buy_quotes.sort(key=lambda q: q.total_cost)
            sell_quotes.sort(key=lambda q: q.total_cost)

            # ── Print status ────────────────────────────────────────────
            logger.info(
                f"──────── t={elapsed:5.0f}s "
                f"[{len(buy_quotes):2d} buy quotes, {len(sell_quotes):2d} sell quotes] ────────"
            )

            if buy_book_now is not None:
                logger.info(f"  Orderbook BUY:  ${buy_book_now:>10.2f}")
            else:
                logger.info("  Orderbook BUY:  N/A")

            if buy_quotes:
                best = buy_quotes[0]
                imp = rfq.calculate_improvement(best.total_cost, buy_book_now) if buy_book_now else 0
                logger.info(
                    f"  Best BUY quote: ${best.total_cost:>10.2f}  "
                    f"improvement {imp:+5.1f}%  (id: ...{best.quote_id[-6:]})"
                )
            else:
                logger.info("  Best BUY quote: (none)")

            logger.info("")

            if sell_book_now is not None:
                logger.info(f"  Orderbook SELL: ${sell_book_now:>10.2f}")
            else:
                logger.info("  Orderbook SELL: N/A")

            if sell_quotes:
                best = sell_quotes[0]
                imp = rfq.calculate_improvement(best.total_cost, sell_book_now) if sell_book_now else 0
                logger.info(
                    f"  Best SELL quote: ${best.total_cost:>10.2f}  "
                    f"improvement {imp:+5.1f}%  (id: ...{best.quote_id[-6:]})"
                )
            else:
                logger.info("  Best SELL quote: (none)")

            logger.info("")
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
    finally:
        logger.info("")
        logger.info("=" * 70)
        logger.info("CANCELLING RFQ...")
        logger.info("=" * 70)
        rfq.cancel_rfq(request_id)
        logger.info(f"RFQ {request_id} cancelled.")
        logger.info("")
        logger.info("Test complete — no quotes were accepted.")


if __name__ == "__main__":
    main()
