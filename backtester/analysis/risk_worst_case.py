#!/usr/bin/env python3
"""
risk_worst_case.py — Worst-case risk analysis for short_strangle_weekly_cap.

Re-runs the top 10 combos from the latest grid backtest using run_single
to recover full trade-level data (strikes, DTE, entry premium, IV).

Three layers of analysis per combo:

  Layer 1 — Hard ceiling
    Maximum possible loss = target_max_open × avg_entry_premium × stop_loss_pct
    Assumes every open position hits SL simultaneously.

  Layer 2 — Breakeven spot move
    For each recorded trade: how far does spot need to move (%) from entry
    for the strangle to expire at zero profit?  I.e. solve:
        spot_move_pct = (K_put - S0 - premium) / S0  (put side, larger tail)
    Also computes the move that would trigger SL-equivalent loss at expiry.

  Layer 3 — IV shock table
    For a representative position, reprices the strangle with BS under a
    stress grid: spot ±0%, ±5%, ±10%, ±15% × IV multiplier 1.0x, 1.5x, 2.0x, 3.0x.
    Shows whether SL would fire immediately or how close it gets.

Usage:
    python -m backtester.analysis.risk_worst_case
"""
import math
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from backtester.market_replay import MarketReplay
from backtester.engine import run_single
from backtester.strategies.short_strangle_weekly_cap import ShortStrangleWeeklyCap
from backtester.pricing import bs_call, bs_put, HOURS_PER_YEAR

# ------------------------------------------------------------------
# Top 10 combos extracted from weekly_strangle_cap_report.html
# Column order: delta, entry_hour, max_daily_new, max_hold_days,
#               stop_loss_pct, take_profit_pct, target_max_open, target_weeks
# ------------------------------------------------------------------

TOP10 = [
    {"delta": 0.20, "entry_hour": 10, "max_daily_new": 3, "max_hold_days": 0,  "stop_loss_pct": 2.0, "take_profit_pct": 0.40, "target_max_open": 5, "target_weeks": 1},
    {"delta": 0.20, "entry_hour": 10, "max_daily_new": 3, "max_hold_days": 0,  "stop_loss_pct": 3.0, "take_profit_pct": 0.40, "target_max_open": 5, "target_weeks": 1},
    {"delta": 0.20, "entry_hour": 10, "max_daily_new": 3, "max_hold_days": 7,  "stop_loss_pct": 3.0, "take_profit_pct": 0.40, "target_max_open": 5, "target_weeks": 1},
    {"delta": 0.20, "entry_hour": 10, "max_daily_new": 3, "max_hold_days": 7,  "stop_loss_pct": 2.0, "take_profit_pct": 0.40, "target_max_open": 5, "target_weeks": 1},
    {"delta": 0.20, "entry_hour": 10, "max_daily_new": 3, "max_hold_days": 0,  "stop_loss_pct": 2.0, "take_profit_pct": 0.40, "target_max_open": 3, "target_weeks": 1},
    {"delta": 0.20, "entry_hour": 10, "max_daily_new": 3, "max_hold_days": 7,  "stop_loss_pct": 2.0, "take_profit_pct": 0.40, "target_max_open": 3, "target_weeks": 1},
    {"delta": 0.20, "entry_hour": 10, "max_daily_new": 3, "max_hold_days": 0,  "stop_loss_pct": 3.0, "take_profit_pct": 0.40, "target_max_open": 3, "target_weeks": 1},
    {"delta": 0.20, "entry_hour": 10, "max_daily_new": 3, "max_hold_days": 7,  "stop_loss_pct": 3.0, "take_profit_pct": 0.40, "target_max_open": 3, "target_weeks": 1},
    {"delta": 0.20, "entry_hour": 10, "max_daily_new": 2, "max_hold_days": 7,  "stop_loss_pct": 3.0, "take_profit_pct": 0.40, "target_max_open": 3, "target_weeks": 1},
    {"delta": 0.20, "entry_hour": 10, "max_daily_new": 2, "max_hold_days": 7,  "stop_loss_pct": 2.0, "take_profit_pct": 0.40, "target_max_open": 3, "target_weeks": 1},
]

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _breakeven_down(S0, K_put, premium_usd):
    # type: (float, float, float) -> float
    """Spot level where put expires exactly at the money for the premium collected.
    Below this level the position starts losing money at expiry.
    Returns move as negative % from entry spot.
    """
    breakeven_spot = K_put - premium_usd
    return (breakeven_spot - S0) / S0 * 100.0


def _breakeven_up(S0, K_call, premium_usd):
    # type: (float, float, float) -> float
    """Spot level where call expires exactly at the money for the premium collected."""
    breakeven_spot = K_call + premium_usd
    return (breakeven_spot - S0) / S0 * 100.0


def _sl_trigger_spot_down(S0, K_put, premium_usd, sl_pct, T_years, sigma):
    # type: (float, float, float, float, float, float) -> float
    """Estimate the spot move (%) at which the put ask reprices to SL level.
    Uses BS mark (as proxy for mid) — conservative because actual ask > mark.
    SL fires when combined ask >= (1 + sl_pct) × entry_premium.
    We attribute the full SL budget to the put side as a worst-case estimate.
    """
    sl_threshold_usd = (1.0 + sl_pct) * premium_usd
    # Binary search for spot where put_mark equals sl_threshold
    lo, hi = S0 * 0.01, S0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        put_val = bs_put(mid, K_put, T_years, sigma) * mid / S0  # rough USD scale
        if put_val < sl_threshold_usd:
            hi = mid
        else:
            lo = mid
    return (lo - S0) / S0 * 100.0


def _iv_shock_table(S0, K_call, K_put, T_years, sigma_entry, premium_usd, sl_pct):
    # type: (float, float, float, float, float, float, float) -> str
    """Return a formatted table showing combined BS value under spot/IV stress."""
    spot_moves = [0.0, -0.05, -0.10, -0.15, +0.05, +0.10, +0.15]
    iv_mults   = [1.0, 1.5, 2.0, 3.0]
    sl_threshold = (1.0 + sl_pct) * premium_usd

    header = f"{'Spot move':>10s} | " + " | ".join(f"IV×{m:.1f}" for m in iv_mults)
    sep    = "-" * len(header)
    note   = "(values = net P&L per position: negative = profit, positive = loss; SL! = stop-loss triggered)"
    lines  = [note, header, sep]

    for dm in spot_moves:
        S = S0 * (1.0 + dm)
        row = f"{dm*100:>+9.0f}% | "
        cells = []
        for iv_m in iv_mults:
            sigma = sigma_entry * iv_m
            c_val = bs_call(S, K_call, T_years, sigma)
            p_val = bs_put( S, K_put,  T_years, sigma)
            combined_usd = c_val + p_val
            loss_usd = combined_usd - premium_usd  # negative = profit, positive = loss
            sl_flag = " SL!" if combined_usd >= sl_threshold else "    "
            cells.append(f"${loss_usd:>+7.0f}{sl_flag}")
        row += " | ".join(cells)
        lines.append(row)

    return "\n".join(lines)

def main():
    # type: () -> None
    print("Loading market data...")
    replay_opts   = "backtester/data/options_20260309_20260323.parquet"
    replay_spot   = "backtester/data/spot_track_20260309_20260323.parquet"
    replay = MarketReplay(replay_opts, replay_spot)

    print(f"\n{'='*70}")
    print("  WORST-CASE RISK ANALYSIS — short_strangle_weekly_cap (top 10 combos)")
    print(f"{'='*70}\n")

    for rank, params in enumerate(TOP10, 1):
        print(f"\n{'─'*70}")
        print(f"  Combo #{rank}: " + " | ".join(f"{k}={v}" for k, v in sorted(params.items())))
        print(f"{'─'*70}")

        trades = run_single(ShortStrangleWeeklyCap, params, replay)
        if not trades:
            print("  No trades — skipping.")
            continue

        premiums    = [t.entry_price_usd for t in trades]
        avg_premium = sum(premiums) / len(premiums)
        max_premium = max(premiums)

        # Collect per-trade breakeven data
        be_downs, be_ups = [], []
        ivs, dtes = [], []
        rep_trade = None  # pick representative trade for IV shock table

        for t in trades:
            S0        = t.entry_spot
            K_call    = t.metadata.get("call_strike", 0.0)
            K_put     = t.metadata.get("put_strike",  0.0)
            dte_days  = t.metadata.get("dte_at_entry", 14)
            prem      = t.entry_price_usd

            if K_call <= 0 or K_put <= 0 or S0 <= 0:
                continue

            be_down = _breakeven_down(S0, K_put, prem)
            be_up   = _breakeven_up(S0, K_call, prem)
            be_downs.append(be_down)
            be_ups.append(be_up)
            dtes.append(dte_days or 14)

            # Back-solve implied vol from entry premium using bisection
            T_yr = (dte_days or 14) * 24.0 / HOURS_PER_YEAR
            lo_iv, hi_iv = 0.01, 5.0
            for _ in range(60):
                mid_iv = (lo_iv + hi_iv) / 2.0
                model_prem = bs_call(S0, K_call, T_yr, mid_iv) + bs_put(S0, K_put, T_yr, mid_iv)
                if model_prem > prem:
                    hi_iv = mid_iv
                else:
                    lo_iv = mid_iv
                if hi_iv - lo_iv < 0.0001:
                    break
            sigma = (lo_iv + hi_iv) / 2.0
            ivs.append(sigma)

            # Use the trade with most typical DTE as representative
            if rep_trade is None or abs((dte_days or 14) - 14) < abs((rep_trade[4] or 14) - 14):
                rep_trade = (S0, K_call, K_put, T_yr, dte_days, sigma, prem)

        # ── Layer 1: Hard ceiling ─────────────────────────────────
        max_open   = params["target_max_open"]
        sl_pct     = params["stop_loss_pct"]
        ceiling    = max_open * avg_premium * sl_pct
        ceiling_wc = max_open * max_premium * sl_pct

        print(f"\n  LAYER 1 — Hard ceiling (all {max_open} positions hit SL simultaneously)")
        print(f"    Avg entry premium : ${avg_premium:.1f}")
        print(f"    Max entry premium : ${max_premium:.1f}")
        print(f"    Max loss (avg)    : ${ceiling:.0f}   [={max_open} × ${avg_premium:.1f} × {sl_pct}]")
        print(f"    Max loss (worst)  : ${ceiling_wc:.0f}  [={max_open} × ${max_premium:.1f} × {sl_pct}]")

        # ── Layer 2: Breakeven spot moves ─────────────────────────
        if be_downs:
            avg_be_down = sum(be_downs) / len(be_downs)
            avg_be_up   = sum(be_ups)   / len(be_ups)
            worst_be_down = min(be_downs)  # smallest (most adverse) downside move needed
            worst_be_up   = max(be_ups)    # largest upside move needed

            print(f"\n  LAYER 2 — Breakeven spot move at expiry (spot must stay inside)")
            print(f"    Avg breakeven down : {avg_be_down:+.1f}%  (worst: {worst_be_down:+.1f}%)")
            print(f"    Avg breakeven up   : {avg_be_up:+.1f}%  (worst: {worst_be_up:+.1f}%)")
            print(f"    Avg entry DTE      : {sum(dtes)/len(dtes):.0f} days")
            print(f"    Avg implied vol    : {sum(ivs)/len(ivs)*100:.0f}%")
            print(f"    Note: BTC 1-sigma {sum(dtes)/len(dtes):.0f}-day move = "
                  f"±{sum(ivs)/len(ivs) * math.sqrt(sum(dtes)/len(dtes)/365)*100:.1f}% "
                  f"(at avg IV, normal dist)")

        # ── Layer 3: IV shock table ───────────────────────────────
        if rep_trade:
            S0, K_call, K_put, T_yr, dte_d, sigma, prem = rep_trade
            print(f"\n  LAYER 3 — IV shock table (representative position)")
            print(f"    S={S0:,.0f}  K_call={K_call:,.0f}  K_put={K_put:,.0f}  "
                  f"DTE={dte_d}d  IV={sigma*100:.0f}%  Premium=${prem:.1f}")
            print(f"    SL fires when combined buyback >= ${(1+sl_pct)*prem:.1f} "
                  f"({(1+sl_pct)*100:.0f}% of entry)")
            print()
            table = _iv_shock_table(S0, K_call, K_put, T_yr, sigma, prem, sl_pct)
            for line in table.split("\n"):
                print("    " + line)

    print(f"\n{'='*70}")
    print("  Done.")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
