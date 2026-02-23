#!/usr/bin/env python3
"""
RFQ Orderbook Comparison Test

Creates an RFQ for a 25Feb strangle (same as endurance test),
then monitors quotes for 5 minutes WITHOUT accepting any.

Every 10 seconds, prints:
  - Best BUY quote vs orderbook buy cost
  - Best SELL quote vs orderbook sell cost
  - Improvement % for each

This validates that get_orderbook_cost() and calculate_improvement()
produce sensible numbers for both directions.

At the end, cancels the RFQ cleanly.

Usage:
    python test_rfq_comparison.py
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
DTE = 2           # 25Feb
CALL_DELTA = 0.05
PUT_DELTA = -0.05
MONITOR_SECONDS = 300   # 5 minutes
POLL_INTERVAL = 10      # seconds between prints


def resolve_instruments():
    """Find the same strangle instruments as the endurance test."""
    call = select_option(
        expiry_criteria={"dte": DTE},
        strike_criteria={"type": "delta", "value": CALL_DELTA},
        option_type="C",
    )
    put = select_option(
        expiry_criteria={"dte": DTE},
        strike_criteria={"type": "delta", "value": PUT_DELTA},
        option_type="P",
    )
    if not call or not put:
        logger.error("Could not resolve instruments")
        sys.exit(1)
    return call, put


def main():
    call_sym, put_sym = resolve_instruments()
    logger.info(f"Instruments: {call_sym} + {put_sym}")

    legs = [
        OptionLeg(instrument=call_sym, side="BUY", qty=QTY),
        OptionLeg(instrument=put_sym,  side="BUY", qty=QTY),
    ]

    rfq = RFQExecutor()

    # ── Get orderbook baselines for BOTH directions ──────────────────────
    buy_book = rfq.get_orderbook_cost(legs, action="buy")
    sell_book = rfq.get_orderbook_cost(legs, action="sell")

    logger.info("=" * 70)
    logger.info(f"Orderbook BUY  cost (asks): ${buy_book:.2f}" if buy_book else "Orderbook BUY cost: N/A")
    logger.info(f"Orderbook SELL cost (bids): ${sell_book:.2f}" if sell_book else "Orderbook SELL cost: N/A")
    logger.info("=" * 70)

    # ── Create RFQ ───────────────────────────────────────────────────────
    rfq_data = rfq.create_rfq(legs)
    if not rfq_data:
        logger.error("Failed to create RFQ")
        sys.exit(1)

    request_id = rfq_data["requestId"]
    logger.info(f"RFQ created: {request_id}")
    logger.info(f"Monitoring for {MONITOR_SECONDS}s — will NOT accept any quote")
    logger.info("")

    # ── Monitor loop ─────────────────────────────────────────────────────
    start = time.time()
    iteration = 0

    try:
        while time.time() - start < MONITOR_SECONDS:
            elapsed = time.time() - start
            quotes = rfq.get_quotes(request_id)
            now_ms = int(time.time() * 1000)

            # Refresh orderbook each iteration for accurate comparison
            buy_book_now = rfq.get_orderbook_cost(legs, action="buy")
            sell_book_now = rfq.get_orderbook_cost(legs, action="sell")

            # Separate quotes by direction
            buy_quotes = []
            sell_quotes = []
            for q in quotes:
                if q.state != "OPEN":
                    continue
                if q.expiry_time and q.expiry_time < now_ms + 1000:
                    continue
                if q.is_we_buy:
                    buy_quotes.append(q)
                elif q.is_we_sell:
                    sell_quotes.append(q)

            # Sort: lowest cost first (best for buying), lowest cost first (most credit for selling)
            buy_quotes.sort(key=lambda q: q.total_cost)
            sell_quotes.sort(key=lambda q: q.total_cost)

            # ── Print report ─────────────────────────────────────────────
            logger.info(f"─── t={elapsed:.0f}s ─── quotes: {len(buy_quotes)} buy, {len(sell_quotes)} sell ───")

            if buy_book_now is not None:
                logger.info(f"  Book BUY  (asks): ${buy_book_now:>8.2f}")
            if sell_book_now is not None:
                logger.info(f"  Book SELL (bids): ${sell_book_now:>8.2f}")

            if buy_quotes:
                best = buy_quotes[0]
                imp = rfq.calculate_improvement(best.total_cost, buy_book_now) if buy_book_now else 0
                logger.info(
                    f"  Best BUY  quote: ${best.total_cost:>8.2f}  "
                    f"vs book {imp:+.1f}%  "
                    f"(quote {best.quote_id[-6:]})"
                )
            else:
                logger.info("  Best BUY  quote: (none)")

            if sell_quotes:
                best = sell_quotes[0]
                imp = rfq.calculate_improvement(best.total_cost, sell_book_now) if sell_book_now else 0
                logger.info(
                    f"  Best SELL quote: ${best.total_cost:>8.2f}  "
                    f"vs book {imp:+.1f}%  "
                    f"(quote {best.quote_id[-6:]})"
                )
            else:
                logger.info("  Best SELL quote: (none)")

            logger.info("")
            iteration += 1
            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        logger.info("\nInterrupted by user")
    finally:
        logger.info(f"Cancelling RFQ {request_id}...")
        rfq.cancel_rfq(request_id)
        logger.info("Done — no quotes were accepted.")


if __name__ == "__main__":
    main()
