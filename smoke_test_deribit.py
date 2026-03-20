#!/usr/bin/env python3
"""Deribit production smoke test — validates auth, data, account without trading."""

import sys

print("=" * 60)
print("DERIBIT PRODUCTION SMOKE TEST")
print("=" * 60)

# 1. Config
from config import EXCHANGE, ENVIRONMENT, DERIBIT_BASE_URL
print(f"\n1. Config")
print(f"   Exchange:    {EXCHANGE}")
print(f"   Environment: {ENVIRONMENT}")
print(f"   Base URL:    {DERIBIT_BASE_URL}")
assert EXCHANGE == "deribit", f"Expected deribit, got {EXCHANGE}"
assert ENVIRONMENT == "production", f"Expected production, got {ENVIRONMENT}"
assert "test" not in DERIBIT_BASE_URL, "URL contains test!"
print("   OK")

# 2. Auth
from exchanges import build_exchange
components = build_exchange()
auth = components["auth"]
print(f"\n2. Auth")
resp = auth.call("public/test", {})
assert "result" in resp, f"Auth test failed: {resp}"
print(f"   public/test: {resp.get('result')}")
print("   OK")

# 3. Index price via adapter
md = components["market_data"]
price = md.get_index_price("BTC", use_cache=False)
print(f"\n3. Index Price (adapter)")
print(f"   BTC index: ${price:,.2f}")
assert price and price > 50000, f"Price looks wrong: {price}"
print("   OK")

# 4. Index price via convenience function
from market_data import get_btc_index_price
price2 = get_btc_index_price(use_cache=False)
print(f"\n4. Index Price (convenience fn)")
print(f"   get_btc_index_price(): ${price2:,.2f}")
assert abs(price - price2) < 100, f"Prices diverge: {price} vs {price2}"
print("   OK")

# 5. Account
acct = components["account_manager"]
info = acct.get_account_info(force_refresh=True)
print(f"\n5. Account")
print(f"   Equity:    ${info['equity']:,.2f}")
print(f"   Margin:    ${info.get('initial_margin', 0):,.2f}")
print(f"   Currency:  {info.get('currency', '?')}")
positions = acct.get_positions(force_refresh=True)
print(f"   Positions: {len(positions)}")
print("   OK")

# 6. Option instruments
instruments = md.get_option_instruments("BTC")
print(f"\n6. Instruments")
print(f"   BTC options: {len(instruments)}")
assert len(instruments) > 0, "No instruments!"
print("   OK")

# 7. Strategy
from strategies.straddle_10utc import straddle_10utc
cfg = straddle_10utc()
print(f"\n7. Strategy")
print(f"   Name:   {cfg.name}")
print(f"   Entry:  {[c.__name__ for c in cfg.entry_conditions]}")
print(f"   Exit:   {[c.__name__ for c in cfg.exit_conditions]}")
print(f"   Legs:   {[(l.option_type, l.side, l.qty) for l in cfg.legs]}")
phases = cfg.execution_params.phases
for i, p in enumerate(phases):
    print(f"   Phase {i+1}: {p.pricing} {p.duration_seconds}s reprice={p.reprice_interval}s buf={p.buffer_pct}%")
print("   OK")

print("\n" + "=" * 60)
print("ALL CHECKS PASSED")
print("=" * 60)
