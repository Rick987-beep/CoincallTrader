"""
Test 5: Deribit Symbol Translation — Round Trip
=================================================
Parses every active BTC option instrument_name into components,
reconstructs it, and verifies a perfect round-trip.

Also catalogs edge cases: decimal strikes, daily expiries, etc.

Run:  python tests/deribit/test_deribit_symbols.py [--prod]
"""
import requests
import json
import re
import sys
from datetime import datetime, timezone

# ── Config ──────────────────────────────────────────────────────────────
USE_PROD = "--prod" in sys.argv
BASE_URL = "https://www.deribit.com" if USE_PROD else "https://test.deribit.com"
ENV_LABEL = "PRODUCTION" if USE_PROD else "TESTNET"

MONTH_MAP = {
    "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
    "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
}
MONTH_REV = {v: k for k, v in MONTH_MAP.items()}

def api_get(method, params=None):
    resp = requests.get(
        f"{BASE_URL}/api/v2/public/{method}",
        params=params or {},
        timeout=15,
    )
    data = resp.json()
    if "error" in data:
        print(f"  ERROR: {data['error']}")
        return None
    return data.get("result")

def separator(title):
    print(f"\n{'='*70}")
    print(f"  {title}")
    print(f"{'='*70}\n")


# ── Symbol Parser ───────────────────────────────────────────────────────
# Deribit format: BTC-20MAR26-74000-C
#   underlying-DDMMMYY-strike-type
OPTION_RE = re.compile(
    r"^([A-Z]+)-(\d{1,2})([A-Z]{3})(\d{2})-(\d+(?:\.\d+)?)-([CP])$"
)

def parse_instrument(name):
    """Parse a Deribit instrument name into components.
    Returns (underlying, day, month, year2, strike, option_type) or None."""
    m = OPTION_RE.match(name)
    if not m:
        return None
    underlying = m.group(1)
    day = int(m.group(2))
    month_str = m.group(3)
    year2 = int(m.group(4))
    strike = m.group(5)  # keep as string to preserve format
    opt_type = m.group(6)
    return (underlying, day, month_str, year2, strike, opt_type)

def reconstruct(parsed):
    """Reconstruct a Deribit symbol from parsed components."""
    underlying, day, month_str, year2, strike, opt_type = parsed
    # Strike: Deribit uses integer strikes (no decimal point) for whole numbers
    strike_val = float(strike)
    if strike_val == int(strike_val):
        strike_fmt = str(int(strike_val))
    else:
        strike_fmt = strike
    return f"{underlying}-{day}{month_str}{year2}-{strike_fmt}-{opt_type}"


# ────────────────────────────────────────────────────────────────────────
separator(f"TEST 5: Symbol Translation ({ENV_LABEL})")

instruments = api_get("get_instruments", {
    "currency": "BTC", "kind": "option", "expired": "false"
})
if not instruments:
    print("Failed to fetch instruments"); sys.exit(1)

print(f"  Total active BTC options: {len(instruments)}")

# ── 5.1: Round-trip every instrument ────────────────────────────────────
separator("TEST 5.1: Parse + Reconstruct Round Trip")
passed = 0
failed = 0
parse_failures = []
round_trip_failures = []
all_strikes = set()
all_expiries = set()
decimal_strikes = []

for inst in instruments:
    name = inst["instrument_name"]
    parsed = parse_instrument(name)
    if parsed is None:
        parse_failures.append(name)
        failed += 1
        continue

    reconstructed = reconstruct(parsed)
    if reconstructed != name:
        round_trip_failures.append((name, reconstructed))
        failed += 1
    else:
        passed += 1

    underlying, day, month_str, year2, strike, opt_type = parsed
    all_strikes.add(float(strike))
    all_expiries.add(f"{day}{month_str}{year2}")

    # Detect decimal strikes
    if "." in strike:
        decimal_strikes.append(name)

print(f"  Round-trip: {passed} passed, {failed} failed out of {len(instruments)}")

if parse_failures:
    print(f"\n  ── Parse failures ({len(parse_failures)}) ──")
    for name in parse_failures[:10]:
        print(f"    {name}")

if round_trip_failures:
    print(f"\n  ── Round-trip mismatches ({len(round_trip_failures)}) ──")
    for orig, reco in round_trip_failures[:10]:
        print(f"    {orig}  →  {reco}")

# ── 5.2: Catalog expiries and strikes ──────────────────────────────────
separator("TEST 5.2: Expiry & Strike Catalog")
sorted_expiries = sorted(all_expiries, key=lambda e: (
    int(e[-2:]),  # year
    MONTH_MAP.get(e[1:4] if len(e) == 6 else e[2:5], 0),  # month
    int(e[:1] if len(e) == 6 else e[:2])  # day
))
print(f"  Unique expiries ({len(sorted_expiries)}):")
for exp in sorted_expiries:
    count = sum(1 for i in instruments if exp in i["instrument_name"])
    print(f"    {exp}  ({count} instruments)")

print(f"\n  Strike range: {min(all_strikes):,.0f} — {max(all_strikes):,.0f}")
print(f"  Unique strikes: {len(all_strikes)}")

if decimal_strikes:
    print(f"\n  ── Decimal strikes ({len(decimal_strikes)}) ──")
    for s in decimal_strikes[:10]:
        print(f"    {s}")
else:
    print(f"\n  No decimal strikes found — all integers")

# ── 5.3: Non-option instruments check ──────────────────────────────────
separator("TEST 5.3: Non-Option Instruments (filtered out)")
# Also fetch futures to see what other formats exist
futures = api_get("get_instruments", {
    "currency": "BTC", "kind": "future", "expired": "false"
})
if futures:
    print(f"  Active BTC futures: {len(futures)}")
    for f in futures:
        print(f"    {f['instrument_name']}  (kind={f['kind']})")
    # Verify our parser correctly rejects these
    for f in futures:
        result = parse_instrument(f["instrument_name"])
        if result is not None:
            print(f"    WARNING: parser incorrectly parsed future: {f['instrument_name']}")

# ── 5.4: Expiry timestamp consistency ──────────────────────────────────
separator("TEST 5.4: Expiry Timestamp vs Parsed Date")
mismatches = 0
checked = 0
for inst in instruments[:50]:  # check a sample
    name = inst["instrument_name"]
    parsed = parse_instrument(name)
    if not parsed:
        continue
    _, day, month_str, year2, _, _ = parsed
    month = MONTH_MAP.get(month_str, 0)
    year = 2000 + year2
    # Deribit expiry timestamps are at 08:00 UTC on expiry day
    expected_date = datetime(year, month, day, tzinfo=timezone.utc)
    actual_ts = inst["expiration_timestamp"]
    actual_date = datetime.fromtimestamp(actual_ts / 1000, tz=timezone.utc)
    if actual_date.date() != expected_date.date():
        print(f"  MISMATCH: {name} → parsed {expected_date.date()} vs actual {actual_date.date()}")
        mismatches += 1
    checked += 1

print(f"  Checked {checked} instruments: {mismatches} date mismatches")
if mismatches == 0:
    print(f"  ✓ All parsed dates match expiration_timestamp dates")

# ────────────────────────────────────────────────────────────────────────
separator("TEST 5 SUMMARY")
print(f"Environment: {ENV_LABEL} ({BASE_URL})")
print(f"Total instruments: {len(instruments)}")
print(f"Round-trip passed: {passed}")
print(f"Round-trip failed: {failed}")
print(f"Parse failures: {len(parse_failures)}")
print(f"Decimal strikes: {len(decimal_strikes)}")
print(f"Date mismatches: {mismatches}")
total_fail = failed + mismatches
print(f"\nTest 5: {'PASSED ✓' if total_fail == 0 else 'FAILED ✗'}")
