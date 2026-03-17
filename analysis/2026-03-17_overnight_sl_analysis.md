# Overnight SL Incident Analysis — March 17, 2026

## Summary

Trade `0f607bf2-90d` (daily_put_sell): SELL 0.8× BTCUSD-18MAR26-72000-P.
Opened at 03:01, stopped out at 03:23. **PnL: −$52.00** (−68.4% ROI, 22.9 min hold).  
Root cause: legitimate market move (BTC dropped ~$450 in 22 min), but **two bugs compounded the damage**.

---

## Timeline (all UTC, March 17 2026)

| Time | Event | BTC Spot | Option Mark |
|------|-------|----------|-------------|
| 03:00:03 | Entry gates pass (EMA OK, time window OK) | $75,170 | — |
| 03:00:16 | Resolved leg: BTCUSD-18MAR26-72000-P (1DTE, ~10Δ put) | — | — |
| 03:00:18 | RFQ created (SELL), Phase 1 wait=30s | — | — |
| 03:00:52 | RFQ quote accepted (Phase 2): **$76.00 total** | — | — |
| 03:01:05 | **Trade opened**: SELL @ $95.00/contract, qty=0.8 | $75,220 | ~$95 |
| 03:01:06 | TP limit buy placed: order `…7488` @ **$9.50** (90% capture) | — | — |
| 03:01:54 | ⚠️ First reconciliation warning (repeats every ~60s for 20 min) | — | — |
| 03:10 | BTC decline accelerates | $75,121 | — |
| 03:12 | BTC breaks $75k | $74,999 | — |
| 03:17 | | $74,913 | — |
| 03:22:00 | Sharp red candle | $74,886→$74,732 | — |
| 03:22:37 | **SL triggered**: `max_loss(70%,mark)` PnL ratio=−76.3% | ~$74,800 | **~$167** |
| 03:22:37 | Start RFQ close (BUYING back) | — | — |
| 03:22:42 | Best RFQ quote: $137.60 (0.8×$172), much worse than book ($116) | — | — |
| 03:22:55 | **RFQ close FAILED** (15s timeout, no acceptable quotes) | — | — |
| 03:22:56 | Fallback to limit — **idempotent hit returns TP order @ $9.50** | — | — |
| 03:23:31 | LimitFillManager timeout (35s), requotes | — | — |
| 03:23:33 | New order placed @ $163.20 | — | — |
| 03:23:44 | **FILLED at $160.00/contract** | $74,849 | $156.89 |

**Total time SL-trigger → fill: 67 seconds** (should have been ~5–15s).

---

## Exchange-Confirmed Fill Data (Coincall API)

### Opening (RFQ, order `2033740322869219328`)
| Field | Value |
|-------|-------|
| type | RFQ (tradeType=14) |
| side | SELL |
| price (submitted) | $120.91 (mark at RFQ time) |
| **avgPrice (fill)** | **$95.00** |
| qty | 0.8 |
| fee | $9.50 |
| **premium received** | **$76.00** (0.8 × $95) |

### TP Order (`2033740379135807488`) — never filled
| Field | Value |
|-------|-------|
| side | BUY |
| price (exchange) | **$9.00** (bot requested $9.50 — exchange truncated) |
| fillQty | 0 |
| state | 3 (cancelled at 03:23:32) |

### SL Close (`2033746031354765312`)
| Field | Value |
|-------|-------|
| side | BUY |
| price (submitted) | $163.00 |
| **avgPrice (fill)** | **$160.00** |
| qty | 0.8 |
| fee | $11.97 |
| rpnl | **−$52.00** |
| markPrice at fill | $156.89 |

---

## BTC Price Action (Binance 1min candles)

```
03:00  $75,198 → $75,226     ← trade opened here (OTM by $3,220 = 4.3%)
03:05  $75,278 → $75,187     ← first dip
03:10  $75,191 → $75,121     ← slide accelerates
03:12  $75,090 → $74,999     ← broke $75k
03:14  $75,038 → $74,944
03:17  $74,994 → $74,913
03:21  $74,956 → $74,886
03:22  $74,886 → $74,732 LOW ← sharp selling, SL triggered here
03:23  $74,777 → $74,849     ← small bounce (we filled here)
```

**Total move: −$450 (−0.6%) in 22 minutes.** Still OTM by ~$2,770 at trigger time.

---

## Question 1: Was the SL trigger correct?

**Yes — the mark-based SL triggered legitimately.**

- Entry: SELL at $95.00/contract
- Mark at trigger: ~$167.44 (derived: PnL=−$57.96 / 0.8 = $72.44 loss/contract → $95 + $72.44)
- PnL ratio: −$57.96 / $76.00 = −**76.3%** (threshold: −70%)  

Why did a 0.6% BTC move cause a 76% put-price increase?
| Factor | Contribution |
|--------|-------------|
| Delta (−0.10→−0.15) | $450 × 0.12 avg ≈ $54/BTC of option |
| Gamma acceleration | delta doubled as BTC approached strike |
| IV spike | fear-driven vol increase during 22-min selloff |
| **Combined** | $95 → $167 (+76%) on 1DTE near-money put = **reasonable** |

### Would `pnl_mode="executable"` have changed anything?

Probably not. The eventual fill was $160/contract, which implies executable PnL at trigger time was:
- (95 − 160) × 0.8 = −$52 → ratio = −68.4%  

Very close to the 70% threshold. The SL would have triggered within 1–2 more ticks regardless.  
**Mark mode triggered ~1 tick earlier** because mark ($167) was slightly elevated vs executable ($160), which is expected — mark = mid, executable = ask (which is typically better for buyer in a one-sided market).

---

## Question 2: Did the RFQ/execution process go wrong?

**Yes — two problems compounded to add ~50 seconds of delay.**

### Problem A: RFQ close failed (added 18s)
- All RFQ quotes were **much worse** than orderbook (best: $172/contract vs book: $145/contract)
- In fast-moving markets, RFQ market-makers widen or don't compete
- The 15s RFQ timeout is by design, but it's 15s of watching the market move against you
- The "vs book" percentages in the log (−218.6%) look extreme because of the baseline math; actual premium was ~18% worse than orderbook

### Problem B: Idempotent order collision (added 35s) ← **BUG**

When the SL close fell back to limit mode:
1. `LimitFillManager` asked `OrderManager` to place a buy @ $168.30
2. `OrderManager` found the existing **TP order** (`…7488` @ $9.50) for the same `(lifecycle, leg, purpose=close_leg)` key
3. It returned that order via **idempotent dedup** instead of placing a new aggressive one
4. `LimitFillManager` waited 35s for the $9.50 order to fill (it never would at $160 market)
5. Only after the 30s timeout did it cancel and requote at $163.20
6. The new order filled instantly at $160.00

**This is a design flaw**: both TP and SL close use `purpose=close_leg`, so the idempotent check treats them as the same order. The SL path should either cancel the TP first, or use a different purpose key.

**Cost of the delay**: BTC moved from ~$74,800 to ~$74,849 during the extra 35s (actually favorable in this case by ~$3–5 per contract, but could easily go the opposite way).

---

## Question 3: Reconciliation bug (Telegram spam)

**Confirmed: snake_case vs camelCase key mismatch.**

### The bug

`account_manager.get_open_orders()` transforms the API response:
```python
order_info = {
    'order_id': order.get('orderId'),   # ← stored as snake_case
    ...
}
```

`order_manager.reconcile()` looks for camelCase:
```python
exchange_ids = {str(o.get("orderId", "")) for o in exchange_open_orders}
#                            ^^^^^^^^ — never matches snake_case keys
```

**Result**: `exchange_ids` is always `{""}`, so every live order appears "not found on exchange". This triggers:
- A warning log every ~60 seconds
- A Telegram alert every ~60 seconds  
- A `poll_order()` call (which works fine, but doesn't fix the comparison)

### Impact last night

22 consecutive "Reconciliation: 1 issue(s) found" warnings from 03:01:54 to 03:22:01 (one per tick for 20 minutes), all for the TP order `…7488`.

### Fix

In `order_manager.py`, line 588, change:
```python
exchange_ids = {str(o.get("orderId", "")) for o in exchange_open_orders}
```
to:
```python
exchange_ids = {str(o.get("orderId", o.get("order_id", ""))) for o in exchange_open_orders}
```
Or normalize the key in `get_open_orders()` to include both.

---

## Minor Issue: TP Order Price Truncation

- Bot calculated TP: $9.50 (entry $95 × 10%)
- Exchange stored: **$9.00** (truncated to integer?)
- No impact on this trade (never would have filled), but worth checking if the exchange drops decimals for options < $10

---

## Issue Summary

| # | Issue | Severity | Impact This Trade |
|---|-------|----------|-------------------|
| 1 | **Idempotent TP/SL collision** | HIGH | +35s close delay |
| 2 | **Reconciliation camelCase bug** | MEDIUM | 22 false warnings, Telegram spam |
| 3 | RFQ close fails in fast markets | LOW (by design) | +18s, but has limit fallback |
| 4 | TP price truncation ($9.50→$9.00) | LOW | None this time |

---

## Financial Summary

| Metric | Value |
|--------|-------|
| Strategy | daily_put_sell |
| Instrument | BTCUSD-18MAR26-72000-P (1DTE put, strike 72k) |
| Entry | SELL @ $95.00/contract via RFQ |
| Exit | BUY @ $160.00/contract via limit (after RFQ failure) |
| Qty | 0.8 BTC |
| Premium received | $76.00 |
| Buyback cost | $128.00 (0.8 × $160) |
| Fees | $21.47 ($9.50 open + $11.97 close) |
| **Realized PnL** | **−$52.00** (before fees: −$52, after fees: −$73.47) |
| **ROI** | **−68.4%** (of premium received) |
| Hold time | 22.9 minutes |

---

*Analysis performed on production VPS logs + Coincall API + Binance 1min candles.*  
*Generated: 2026-03-17*
