# Fee-Inclusive PnL & Exchange Trade Log Access

**Status:** Implemented (Apr 19, 2026)  
**Since:** v1.16.0 (execution layer refactor added inline fee capture)  
**Scope:** Live trading application (all slots, both exchanges)

---

## Background & What Changed

This document was originally written (pre-v1.16.0) when the application had **no fee capture at all**. The proposed solution was an elaborate post-close reconciler that would query exchange trade logs as the *primary* fee source.

**v1.16.0 solved 80% of the problem inline.** The execution layer refactor introduced:
- `execution/fees.py` — `extract_fee()` parses the `_trades` array from order responses at fill time
- `Price(amount, currency)` value type with `Currency` enum (BTC, USD, ETH)
- `OrderRecord.fee` — fee stored per order in the order ledger
- `TradeLifecycle.open_fees` / `close_fees` / `total_fees` — fee aggregation at trade level

We now capture fees **at action time** from the order response — the same moment we learn the fill price. This is the correct engineering approach: record what you know when you know it.

A live latency test on Deribit testnet (Apr 19, BTC-26JUN26-75000-C ATM call) confirmed that `_trades` with full fill details including fees appear **in the order response itself** — the exchange trade log is redundant for fee capture. No second reconciliation step is needed.

---

## Implementation — Fee-Inclusive PnL

### Problem (solved)

`_finalize_close()` previously computed only **gross** PnL. Fees were already captured in `trade.open_fees` and `trade.close_fees` (since v1.16.0) but were not deducted.

### Why this matters

From the Apr 13–19 slot-02 analysis (short_strangle_delta_tp on Deribit):
- 5 trades incurred **0.00751 BTC ($565)** in total fees
- Fees consumed **26.5%** of the gross profit on the profitable subset
- A single trade reported PnL of 0.006 BTC; actual net was 0.00538 BTC — **10.3% overstatement**

Fees are material. Gross PnL is misleading.

### Implementation (Apr 19, 2026)

`_finalize_close()` in `trade_lifecycle.py` now computes both gross and net:

```python
def _finalize_close(self) -> None:
    self.exit_cost = self.total_exit_cost()
    entry = self.total_entry_cost()
    self.realized_pnl_gross = -(entry + self.exit_cost)
    fees = float(self.total_fees) if self.total_fees else 0.0
    self.realized_pnl = self.realized_pnl_gross - fees
```

**Files changed:**
- **`trade_lifecycle.py`** — added `realized_pnl_gross` field; `_finalize_close()` deducts fees; `to_dict()`/`from_dict()` round-trip the new field
- **`persistence.py`** — JSONL records now include `realized_pnl_gross`, `total_fees`, `fee_denomination`
- **`lifecycle_engine.py`** — structured TRADE_CLOSED events include `realized_pnl_gross`
- **Tests** — 3 new tests (`test_finalize_close_deducts_fees`, `test_finalize_close_deducts_btc_fees`, updated round-trip); all 511 pass

**Backward compatibility:** `realized_pnl_gross` defaults to `None` on deserialization — old snapshots and JSONL records load without error. `realized_pnl` (the canonical field) is now net; all downstream consumers (logging, Telegram, strategy stats) automatically get the correct number.

**Denomination safety:** Fees and PnL are always in the same currency (BTC for Deribit, USD for Coincall). `sum_fees()` raises `DenominationError` on mismatch. BTC precision is preserved at 8+ decimal places.

---

## Related: Trade Blotter

Durable trade history, queryable trade log, and dashboard visibility are covered in a separate upgrade document: [`trade-blotter.md`](trade-blotter.md).

---

## Empirical Findings (Apr 13–19, 2026 — slot-02)

These production observations informed the design. Preserved here for reference.

### Fee magnitude
- 5 short-strangle trades: **0.00751 BTC ($565)** total fees
- Fees consumed 26.5% of gross profit on the profitable subset (Tue–Fri)
- Single-trade overstatement: 10.3% (reported 0.006 BTC, actual net 0.00538 BTC)

### Fill price accuracy
- Strategy-recorded fill prices matched Deribit's confirmed prices exactly
- The entire PnL discrepancy was attributable to fees, not price estimation error
- Conclusion: our fill price capture is reliable; fee capture was the missing piece (now solved by v1.16.0)

### Partial fills
- 5× BTC-18APR26-79000-C filled as 3 chunks: 3.8 + 0.6 + 0.6
- 5× BTC-18APR26-76000-P filled as 3 chunks: 1.8 + 2.7 + 0.5
- `extract_fee()` already handles multi-fill aggregation from the `_trades` array

### BTC-native precision
- Deribit fees are tiny BTC fractions (e.g. 0.00027707 BTC per fill)
- The `Price` type preserves native denomination — no premature USD conversion
- 8 decimal places (satoshi resolution) for BTC values

### 4. Transaction log `change` field ≠ simple qty × price
- The `change` field in Deribit's transaction log is the net cashflow **after deducting fees**. For example: selling 5× at 0.0007 gives `change = 0.00313543`, not `5 × 0.0007 = 0.0035`. The difference is the fee.
- This is useful as a **cross-check** but the reconciler should compute from individual trade records, not from `change`.

### 5. Delivery events have no order/trade ID
- Options expiring worthless appear as `type=delivery` with `price=0.0`, `change=0.0`, `side=close buy`, and **no order_id**.
- The reconciler's expiry-settled path is correct: skip close-leg reconciliation entirely for deliveries. There is nothing to query.

### 6. Strategy log durability
- A redeploy on Apr 17 wiped trade_history.jsonl, losing trades #1-4.
- **Addressed by:** the durable trade blotter design in [`trade-blotter.md`](trade-blotter.md).
