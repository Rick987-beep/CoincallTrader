#!/usr/bin/env python3
"""
Test: Complex Option Selection Criteria

Validates both the manual filtering pipeline and the find_option()
public API against the live Coincall API.

Steps 1-5: Manual pipeline — exercises internal helpers directly.
  Criteria: BTCUSD puts, 6-13 days, below ATM, -0.45 < delta < -0.15,
  strike < index * 0.995.

Step 6: find_option() — compound criteria in a single call.
  Criteria: BTCUSD calls, 7-21 days (target ~14d), 2%+ above ATM,
  delta target 0.30.

Run:
    python3 tests/test_complex_option_selection.py
"""

import logging
import os
import sys
import time

# Ensure project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from market_data import get_btc_futures_price, get_option_instruments, get_option_details
from option_selection import select_option, find_option, _filter_by_expiry, _add_delta_to_options

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── Results tracker ──────────────────────────────────────────────────────────
_results = []


def record(name, passed, detail=""):
    _results.append((name, passed, detail))
    sym = "\u2713" if passed else "\u2717"
    print(f"  {sym} {name}" + (f"  ({detail})" if detail else ""))


# ── Test criteria constants ──────────────────────────────────────────────────
UNDERLYING = "BTC"
OPTION_TYPE = "P"            # puts only
EXPIRY_MIN_DAYS = 6
EXPIRY_MAX_DAYS = 13
DELTA_LOWER = -0.45          # delta must be  > -0.45  (closer to zero = more OTM)
DELTA_UPPER = -0.15          # delta must be  < -0.15  (further from zero = more ITM)
STRIKE_DISCOUNT = 0.995      # strike must be < index_price * 0.995


# =============================================================================
# Step 1 — Fetch index price
# =============================================================================

def test_fetch_index_price():
    """Fetch BTC/USDT futures price (proxy for index)."""
    print("\n\u2500\u2500 Step 1: Fetch index price \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    price = get_btc_futures_price(use_cache=False)
    ok = price is not None and price > 0
    record("Index price retrieved", ok, f"${price:,.2f}" if ok else "None")
    return price


# =============================================================================
# Step 2 — Fetch instruments & filter by expiry (6-13 days, puts only)
# =============================================================================

def test_expiry_filter():
    """Get all BTC options and filter to 6\u201313 day window, puts only."""
    print("\n\u2500\u2500 Step 2: Expiry filter (6\u201313 days, puts) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    instruments = get_option_instruments(UNDERLYING)
    record("Instruments fetched", instruments is not None and len(instruments) > 0,
           f"{len(instruments)} total" if instruments else "None")
    if not instruments:
        return None

    expiry_criteria = {"minExp": EXPIRY_MIN_DAYS, "maxExp": EXPIRY_MAX_DAYS}
    filtered = _filter_by_expiry(instruments, expiry_criteria, OPTION_TYPE)
    record("Expiry filter returned options", len(filtered) > 0, f"{len(filtered)} options")

    # Verify every option actually falls in the window
    now_ms = time.time() * 1000
    min_ms = now_ms + EXPIRY_MIN_DAYS * 86400_000
    max_ms = now_ms + EXPIRY_MAX_DAYS * 86400_000
    all_in_range = all(min_ms <= o["expirationTimestamp"] <= max_ms for o in filtered)
    record("All expirations within 6\u201313 day window", all_in_range)

    # Verify all are puts
    all_puts = all(o["symbolName"].endswith("-P") for o in filtered)
    record("All filtered options are puts", all_puts)

    return filtered


# =============================================================================
# Step 3 — Apply compound strike + delta filter
# =============================================================================

def test_compound_strike_filter(expiry_options, index_price):
    """
    From the expiry-filtered puts, find the best option satisfying:
      1. strike < index_price           (below ATM)
      2. strike < index_price * 0.995   (at least 0.5 % discount)
      3. -0.45 < delta < -0.15          (moderate OTM put)

    Of all candidates, pick the one whose delta is closest to -0.30
    (middle of the band) — a reasonable "best" heuristic.
    """
    print("\n\u2500\u2500 Step 3: Compound strike + delta criteria \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    if not expiry_options or not index_price:
        record("Skipped \u2014 missing inputs", False)
        return None

    # 3a — keep only strikes below ATM
    below_atm = [o for o in expiry_options if float(o["strike"]) < index_price]
    record("Strikes below ATM", len(below_atm) > 0,
           f"{len(below_atm)} of {len(expiry_options)}")

    # 3b — strike < index * 0.995
    discount_ceil = index_price * STRIKE_DISCOUNT
    below_discount = [o for o in below_atm if float(o["strike"]) < discount_ceil]
    record(f"Strikes below {STRIKE_DISCOUNT:.3f}\u00d7 index (< ${discount_ceil:,.0f})",
           len(below_discount) > 0, f"{len(below_discount)} options")
    if not below_discount:
        return None

    # 3c — fetch deltas (API call per option, capped at 10 internally)
    enriched = _add_delta_to_options(below_discount)
    record("Delta enrichment", len(enriched) > 0,
           f"{len(enriched)} options with delta")
    if not enriched:
        return None

    # Log all enriched deltas for visibility
    for o in enriched:
        logger.info(f"  {o['symbolName']}  strike={o['strike']}  delta={o.get('delta', 'N/A')}")

    # 3d — apply delta band:  -0.45 < delta < -0.15
    candidates = [
        o for o in enriched
        if o.get("delta") is not None
        and DELTA_LOWER < o["delta"] < DELTA_UPPER
    ]
    record(f"Delta in ({DELTA_LOWER}, {DELTA_UPPER})", len(candidates) > 0,
           f"{len(candidates)} candidates")
    if not candidates:
        # Show what deltas were available so the tester can diagnose
        available = [(o["symbolName"], round(o.get("delta", 0), 4)) for o in enriched]
        record("Available deltas (for diagnosis)", False, str(available))
        return None

    # Pick candidate closest to midpoint of delta band (-0.30)
    delta_midpoint = (DELTA_LOWER + DELTA_UPPER) / 2  # -0.30
    best = min(candidates, key=lambda o: abs(o["delta"] - delta_midpoint))
    record("Best option selected", True,
           f"{best['symbolName']}  strike={best['strike']}  delta={best['delta']:.4f}")
    return best


# =============================================================================
# Step 4 — Validate ALL criteria independently on the winner
# =============================================================================

def test_validate_selection(option, index_price):
    """Re-verify every single criterion on the selected option."""
    print("\n\u2500\u2500 Step 4: Validate selected option \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    if option is None:
        record("No option to validate", False)
        return

    strike = float(option["strike"])
    delta = option.get("delta")
    exp_ts = option["expirationTimestamp"]
    days_to_exp = (exp_ts - time.time() * 1000) / 86400_000

    # Direction
    record("Is a put", option["symbolName"].endswith("-P"))

    # Expiry
    record(f"Expiry in range ({days_to_exp:.1f}d)",
           EXPIRY_MIN_DAYS <= days_to_exp <= EXPIRY_MAX_DAYS)

    # Strike vs ATM
    record(f"Strike ({strike:,.0f}) < ATM ({index_price:,.0f})",
           strike < index_price)

    # Strike discount
    record(f"Strike ({strike:,.0f}) < {STRIKE_DISCOUNT}\u00d7 index ({index_price * STRIKE_DISCOUNT:,.0f})",
           strike < index_price * STRIKE_DISCOUNT)

    # Delta band
    if delta is not None:
        record(f"Delta ({delta:.4f}) > {DELTA_LOWER}",
               delta > DELTA_LOWER)
        record(f"Delta ({delta:.4f}) < {DELTA_UPPER}",
               delta < DELTA_UPPER)
    else:
        record("Delta available", False, "delta is None")


# =============================================================================
# Step 5 — Round-trip via select_option() public API
# =============================================================================

def test_select_option_api():
    """
    Verify the public select_option() returns a symbol for a
    6\u201313 day put with delta target -0.30 (closest match).
    This proves the top-level API works end-to-end with time-based expiry.
    """
    print("\n\u2500\u2500 Step 5: select_option() round-trip \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500")
    symbol = select_option(
        expiry_criteria={"minExp": EXPIRY_MIN_DAYS, "maxExp": EXPIRY_MAX_DAYS},
        strike_criteria={"type": "delta", "value": -0.30},
        option_type="P",
        underlying=UNDERLYING,
    )
    record("select_option() returned a symbol", symbol is not None, symbol or "None")
    if symbol:
        record("Symbol contains BTCUSD", "BTCUSD" in symbol)
        record("Symbol ends with -P", symbol.endswith("-P"))


# =============================================================================
# Step 6 — find_option() compound criteria (new API)
# =============================================================================

def test_find_option():
    """
    Exercise find_option() with user-specified criteria:
      - Call option
      - Expiry: 7–21 days, target ~14 days (mid)
      - Strike: above ATM, at least 2% above index
      - Delta: target 0.30
    """
    print("\n\u2500\u2500 Step 6: find_option() \u2014 OTM call, 1\u20133 wk, \u03b4\u22480.30 \u2500\u2500\u2500\u2500\u2500")
    result = find_option(
        underlying="BTC",
        option_type="C",
        expiry={"min_days": 7, "max_days": 21, "target": "mid"},
        strike={"above_atm": True, "min_distance_pct": 2.0},
        delta={"target": 0.30},
        rank_by="delta_target",
    )
    record("find_option() returned a result", result is not None,
           result["symbolName"] if result else "None")
    if not result:
        return

    # Validate enriched fields
    record("Has symbolName", "symbolName" in result)
    record("Has delta", "delta" in result and result["delta"] is not None)
    record("Has days_to_expiry", "days_to_expiry" in result)
    record("Has distance_pct", "distance_pct" in result)
    record("Has index_price", "index_price" in result)

    sym = result["symbolName"]
    strike = float(result["strike"])
    delta = result["delta"]
    days = result["days_to_expiry"]
    idx = result["index_price"]
    dist = result["distance_pct"]

    # Re-validate all criteria
    record("Is a call", sym.endswith("-C"))
    record("Contains BTCUSD", "BTCUSD" in sym)
    record(f"Expiry {days}d >= 7d", days >= 7)
    record(f"Expiry {days}d <= 21d", days <= 21)
    record(f"Strike ({strike:,.0f}) > ATM ({idx:,.0f})", strike > idx)
    record(f"Strike {dist}% above ATM >= 2%", dist >= 2.0)
    record(f"Delta ({delta:.4f}) is positive", delta > 0)

    # Log summary
    print(f"\n  \u2192 find_option() selected: {sym}")
    print(f"    strike={strike:,.0f}  delta={delta:.4f}  days={days}  dist={dist}%")


# =============================================================================
# Main
# =============================================================================

def main():
    print("=" * 60)
    print("Complex Option Selection \u2014 Compound Criteria Test")
    print("  underlying : BTCUSD")
    print("  direction  : Puts only (Steps 2\u20135) + Call (Step 6)")
    print(f"  put expiry : {EXPIRY_MIN_DAYS}\u2013{EXPIRY_MAX_DAYS} days")
    print(f"  put delta  : ({DELTA_LOWER}, {DELTA_UPPER})")
    print(f"  put strike : < ATM  AND  < index \u00d7 {STRIKE_DISCOUNT}")
    print("  call       : 7\u201321d, 2%+ above ATM, \u03b4 target 0.30")
    print("=" * 60)

    index_price = test_fetch_index_price()
    expiry_options = test_expiry_filter()
    best = test_compound_strike_filter(expiry_options, index_price)
    test_validate_selection(best, index_price)
    test_select_option_api()
    test_find_option()

    # ── Summary ──────────────────────────────────────────────────
    print("\n" + "=" * 60)
    passed = sum(1 for _, ok, _ in _results if ok)
    failed = sum(1 for _, ok, _ in _results if not ok)
    print(f"Results: {passed} passed, {failed} failed, {len(_results)} total")
    if failed:
        print("\nFailed checks:")
        for name, ok, detail in _results:
            if not ok:
                print(f"  \u2717 {name}  ({detail})")
    print("=" * 60)

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
