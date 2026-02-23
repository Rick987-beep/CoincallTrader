#!/usr/bin/env python3
"""
RFQ Iron Condor Comparison Test

Creates an RFQ for a 27Mar iron condor:
  - Inner legs: delta ±0.4
  - Outer legs: $2,000 further away each
  - Mixed sides: SELL inner, BUY outer (net credit structure)

Monitors quotes for 1 minute, printing every 10 seconds:
  - Best BUY and SELL quotes
  - Orderbook comparison for each direction
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

# ── Config ───────────────────────────────────────────────────────────────────

QTY = 0.5
EXPIRY = "27MAR26"
INNER_CALL_DELTA = 0.4
INNER_PUT_DELTA = -0.4
WING_WIDTH = 2000         # outer legs $2000 further OTM
MONITOR_SECONDS = 60
POLL_INTERVAL = 10


def resolve_instruments():
    """Resolve iron condor strikes: inner by delta, outer by offset."""
    # Inner call: ~0.4 delta
    inner_call_sym = select_option(
        expiry_criteria={"symbol": EXPIRY},
        strike_criteria={"type": "delta", "value": INNER_CALL_DELTA},
        option_type="C",
    )
    # Inner put: ~-0.4 delta
    inner_put_sym = select_option(
        expiry_criteria={"symbol": EXPIRY},
        strike_criteria={"type": "delta", "value": INNER_PUT_DELTA},
        option_type="P",
    )
    if not inner_call_sym or not inner_put_sym:
        logger.error("Could not resolve inner leg instruments")
        sys.exit(1)

    # Extract strikes from symbol names (e.g. BTCUSD-27MAR26-70000-C)
    inner_call_strike = int(inner_call_sym.split("-")[2])
    inner_put_strike = int(inner_put_sym.split("-")[2])

    # Outer legs: further OTM
    outer_call_strike = inner_call_strike + WING_WIDTH
    outer_put_strike = inner_put_strike - WING_WIDTH

    # Build outer symbols
    prefix = f"BTCUSD-{EXPIRY}"
    outer_call_sym = f"{prefix}-{outer_call_strike}-C"
    outer_put_sym = f"{prefix}-{outer_put_strike}-P"

    return {
        "inner_call": (inner_call_sym, inner_call_strike),
        "outer_call": (outer_call_sym, outer_call_strike),
        "inner_put": (inner_put_sym, inner_put_strike),
        "outer_put": (outer_put_sym, outer_put_strike),
    }


def main():
    instruments = resolve_instruments()

    ic = instruments["inner_call"]
    oc = instruments["outer_call"]
    ip = instruments["inner_put"]
    op = instruments["outer_put"]

    logger.info(f"Iron Condor structure:")
    logger.info(f"  BUY  {op[0]}  (outer put, {op[1]})")
    logger.info(f"  SELL {ip[0]}  (inner put, {ip[1]})")
    logger.info(f"  SELL {ic[0]}  (inner call, {ic[1]})")
    logger.info(f"  BUY  {oc[0]}  (outer call, {oc[1]})")

    # Define legs — iron condor has mixed sides
    legs = [
        OptionLeg(instrument=op[0], side="BUY",  qty=QTY),  # outer put
        OptionLeg(instrument=ip[0], side="SELL", qty=QTY),   # inner put
        OptionLeg(instrument=ic[0], side="SELL", qty=QTY),   # inner call
        OptionLeg(instrument=oc[0], side="BUY",  qty=QTY),   # outer call
    ]

    rfq = RFQExecutor()

    # ── Orderbook baselines ──────────────────────────────────────────────
    buy_book = rfq.get_orderbook_cost(legs, action="buy")
    sell_book = rfq.get_orderbook_cost(legs, action="sell")

    logger.info("=" * 70)
    if buy_book is not None:
        logger.info(f"Orderbook BUY  cost: ${buy_book:>8.2f}  (we pay to buy the condor)")
    else:
        logger.info("Orderbook BUY  cost: N/A")
    if sell_book is not None:
        logger.info(f"Orderbook SELL cost: ${sell_book:>8.2f}  (we receive to sell the condor)")
    else:
        logger.info("Orderbook SELL cost: N/A")
    logger.info("=" * 70)

    # ── Create RFQ ───────────────────────────────────────────────────────
    rfq_data = rfq.create_rfq(legs)
    if not rfq_data:
        logger.error("Failed to create RFQ — check notional minimum ($50K)")
        sys.exit(1)

    request_id = rfq_data["requestId"]
    logger.info(f"RFQ created: {request_id}")
    logger.info(f"Monitoring for {MONITOR_SECONDS}s — will NOT accept any quote")
    logger.info("")

    # ── Monitor loop ─────────────────────────────────────────────────────
    start = time.time()

    try:
        while time.time() - start < MONITOR_SECONDS:
            elapsed = time.time() - start
            quotes = rfq.get_quotes(request_id)
            now_ms = int(time.time() * 1000)

            # Refresh orderbook
            buy_book_now = rfq.get_orderbook_cost(legs, action="buy")
            sell_book_now = rfq.get_orderbook_cost(legs, action="sell")

            # Separate and sort quotes
            buy_quotes = [q for q in quotes
                          if q.state == "OPEN" and q.is_we_buy
                          and (not q.expiry_time or q.expiry_time > now_ms + 1000)]
            sell_quotes = [q for q in quotes
                           if q.state == "OPEN" and q.is_we_sell
                           and (not q.expiry_time or q.expiry_time > now_ms + 1000)]

            buy_quotes.sort(key=lambda q: q.total_cost)
            sell_quotes.sort(key=lambda q: q.total_cost)

            # ── Print ────────────────────────────────────────────────────
            logger.info(f"─── t={elapsed:.0f}s ─── quotes: {len(buy_quotes)} buy, {len(sell_quotes)} sell ───")

            if buy_book_now is not None:
                logger.info(f"  Book BUY  : ${buy_book_now:>8.2f}")
            if sell_book_now is not None:
                logger.info(f"  Book SELL : ${sell_book_now:>8.2f}")

            if buy_quotes:
                best = buy_quotes[0]
                imp = rfq.calculate_improvement(best.total_cost, buy_book_now) if buy_book_now else 0
                logger.info(
                    f"  Best BUY  : ${best.total_cost:>8.2f}  vs book {imp:+.1f}%  "
                    f"(quote ...{best.quote_id[-6:]})"
                )
            else:
                logger.info("  Best BUY  : (none)")

            if sell_quotes:
                best = sell_quotes[0]
                imp = rfq.calculate_improvement(best.total_cost, sell_book_now) if sell_book_now else 0
                logger.info(
                    f"  Best SELL : ${best.total_cost:>8.2f}  vs book {imp:+.1f}%  "
                    f"(quote ...{best.quote_id[-6:]})"
                )
            else:
                logger.info("  Best SELL : (none)")

            logger.info("")
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        logger.info("\nInterrupted")
    finally:
        logger.info(f"Cancelling RFQ {request_id}...")
        rfq.cancel_rfq(request_id)
        logger.info("Done — no quotes were accepted.")


if __name__ == "__main__":
    main()
