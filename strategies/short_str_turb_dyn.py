"""
Short Strangle Turbulence — Dynamic Quantity

Sells a delta-selected OTM strangle, gated by the Turbulence indicator,
with dynamic contract-quantity sizing based on a USD premium target.

Entry logic:
    1. At entry_hour UTC, start watching on every check cycle.
    2. Each check: composite < turbulence_threshold → open immediately.
    3. Watches from entry_hour until midnight (no hard watch-hours cutoff).
    4. If the score is unavailable (network error, weekend, warmup) → fail-open
       (treat as calm and allow entry).

Dynamic quantity:
    If DYN_TARGET_PREMIUM > 0, the number of contracts per leg is:
        qty = round(min(DYN_TARGET_PREMIUM / combined_premium_usd, MAX_QUANTITY), 1)
    floored at 0.1 (Deribit minimum).  If DYN_TARGET_PREMIUM <= 0, fixed qty=1.

Turbulence indicator:
    - Composite 0–100 score computed from 15-min BTCUSDT klines (Binance public).
    - Green (0–35) = calm / safe to sell.
    - Yellow (35–65) = caution.
    - Red (65–100) = stay out.
    - Fetched + cached by indicators/data.py; indicator computed by indicators/turbulence.py.

Open Execution (profile: strangle_turb_best_effort, two phases, total ≤ 8 min):
    Phase 1 (3 min): both legs quoted at fair price (aggression=0), reprice every 30s.
    Phase 2 (5 min): any unfilled leg re-priced to bid aggressively (aggression=1.0), reprice every 30s.
    On exhaustion: accept whatever structure filled — potentially uneven across legs
    (open_best_effort_exhaustion=True).  Only falls back to FAILED if nothing filled at all.

Close Execution — SL/TP triggered (two phases, total ≤ 90s):
    Phase 1 (30s): both open legs bought at fair price.
    Phase 2 (60s): any remaining unfilled leg re-priced to ask — aggressive.

Close Execution — Max-hold / manual (single aggressive phase, 30s):
    Both legs bought at ask in one shot.
"""

import logging
import math
import time
from datetime import datetime, timezone
from typing import Optional

from indicators.data import fetch_klines
from indicators.turbulence import turbulence as _compute_turbulence
from market_data import get_btc_index_price, get_option_details
from option_selection import strangle, resolve_legs
from strategy import (
    StrategyConfig,
    max_hold_hours,
    weekday_filter,
)
from execution.profiles import get_profile
from trade_lifecycle import RFQParams
from telegram_notifier import get_notifier

logger = logging.getLogger(__name__)


# ─── Strategy Parameters ────────────────────────────────────────────────────
# Overridable via PARAM_* env vars (set by slot .toml config at deploy time).

import os as _os
def _p(name, default, cast=float):
    """Read PARAM_<NAME> from env, falling back to default."""
    return cast(_os.getenv(f"PARAM_{name}", str(default)))

# Structure
DTE   = _p("DTE",   1, int)         # calendar days to target expiry
DELTA = _p("DELTA", 0.15)           # target absolute delta per leg

# Scheduling
ENTRY_HOUR      = _p("ENTRY_HOUR",     19, int)   # UTC hour to start watching
WEEKEND_FILTER  = _p("WEEKEND_FILTER",  1, int)   # 1 = block new opens on Sat/Sun

# Turbulence gate
TURBULENCE_THRESHOLD = _p("TURBULENCE_THRESHOLD", 60.0)   # open only when composite < this

# Risk
STOP_LOSS_PCT    = _p("STOP_LOSS_PCT",    5.0)       # SL fires when combined fair ≥ premium × (1 + pct)
TAKE_PROFIT_PCT  = _p("TAKE_PROFIT_PCT",  0.0)       # TP: close when profit ratio ≥ this (0 = disabled)
MAX_HOLD_HOURS   = _p("MAX_HOLD_HOURS",   0, int)    # force close after N hours; 0 = disabled
MIN_OTM_PCT      = _p("MIN_OTM_PCT",      2.4)       # min OTM distance %; 0 = disabled

# Dynamic quantity
DYN_TARGET_PREMIUM = _p("DYN_TARGET_PREMIUM", 500.0)  # USD target premium; 0 = fixed qty=1
MAX_QUANTITY       = _p("MAX_QUANTITY",        10.0)   # hard cap on contracts per trade

# Open execution
LIMIT_OPEN_FAIR_SECONDS = _p("LIMIT_OPEN_FAIR_SECONDS", 30, int)   # Phase 1: quote at fair
LIMIT_OPEN_AGG_SECONDS  = _p("LIMIT_OPEN_AGG_SECONDS",  30, int)   # Phase 2: aggressive at bid

# SL/TP close execution
CLOSE_FAIR_SECONDS = _p("CLOSE_FAIR_SECONDS", 30, int)       # Phase 1: buy at fair
CLOSE_AGG_SECONDS  = _p("CLOSE_AGG_SECONDS",  60, int)       # Phase 2: aggressive at ask

# Max-hold close execution
HOLD_CLOSE_AGG_SECONDS = _p("HOLD_CLOSE_AGG_SECONDS", 30, int)     # Single aggressive phase at ask

# Operational
MAX_CONCURRENT = DTE + 1                    # 1DTE→2, 2DTE→3, 3DTE→4 (overlap window)
CHECK_INTERVAL = _p("CHECK_INTERVAL", 15, int)


# ─── Dynamic Quantity ────────────────────────────────────────────────────────

def _compute_quantity(combined_premium_usd: float) -> float:
    """
    Compute contracts-per-leg based on DYN_TARGET_PREMIUM.

    If DYN_TARGET_PREMIUM <= 0, returns 1.0 (fixed quantity mode).
    Otherwise: qty = floor(target / combined_premium_usd, step=0.1),
    capped at MAX_QUANTITY, floored at 0.1 (Deribit minimum contract size).
    Uses floor (not round) so we never overshoot the USD target.
    """
    if DYN_TARGET_PREMIUM <= 0 or combined_premium_usd <= 0:
        return 1.0
    raw = DYN_TARGET_PREMIUM / combined_premium_usd
    # Floor to nearest 0.1 — never overshoot target premium
    result = math.floor(raw * 10) / 10
    return max(min(result, MAX_QUANTITY), 0.1)


# Deribit minimum price increment in BTC
_DERIBIT_MIN_TICK = 0.0001

# Minimum assumed price per leg for quantity sizing (BTC).
# When > 0: applied as a floor in _bid_price() fallbacks (bid=0 or both-zero).
# When 0:   floor is disabled — mark is used directly; both-zero returns 0,
#           which causes _compute_quantity() to return 1.0 (fixed-qty fallback).
# Overridable via PARAM_MIN_QTY_PRICE_FLOOR; set to 0 to disable.
MIN_QTY_PRICE_FLOOR = _p("MIN_QTY_PRICE_FLOOR", 0.0002)


def _bid_price(symbol: str) -> float:
    """
    Return the best executable (bid) price for an option, in BTC.

    Used only for quantity sizing in _legs_factory — not for placing orders.
    Fallback ladder for low-liquidity / empty-book legs:
      1. bid > 0                              → use bid directly
      2. bid = 0, mark > 0, floor > 0         → use max(mark, MIN_QTY_PRICE_FLOOR)
      3. bid = 0, mark > 0, floor = 0         → use mark directly
      4. both zero, floor > 0                 → use MIN_QTY_PRICE_FLOOR, log warning
      5. both zero, floor = 0                 → return 0 → _compute_quantity falls back to qty=1.0
    """
    fp = _fair(symbol)
    if fp is None:
        if MIN_QTY_PRICE_FLOOR > 0:
            logger.warning("[ShortStrTurbDyn] _bid_price: no data for %s — using floor %.4f", symbol, MIN_QTY_PRICE_FLOOR)
            return MIN_QTY_PRICE_FLOOR
        logger.warning("[ShortStrTurbDyn] _bid_price: no data for %s — returning 0 (floor disabled)", symbol)
        return 0.0

    bid  = fp.get("bid") or 0.0
    mark = fp.get("mark") or 0.0

    if bid > 0:
        return bid
    if mark > 0:
        if MIN_QTY_PRICE_FLOOR > 0:
            price = max(mark, MIN_QTY_PRICE_FLOOR)
            logger.debug(
                "[ShortStrTurbDyn] _bid_price: %s bid=0, mark=%.4f → using %.4f (floor applied)",
                symbol, mark, price,
            )
            return price
        logger.debug(
            "[ShortStrTurbDyn] _bid_price: %s bid=0, mark=%.4f → using mark directly (floor disabled)",
            symbol, mark,
        )
        return mark

    if MIN_QTY_PRICE_FLOOR > 0:
        logger.warning(
            "[ShortStrTurbDyn] _bid_price: %s bid=0 mark=0 — using floor %.4f",
            symbol, MIN_QTY_PRICE_FLOOR,
        )
        return MIN_QTY_PRICE_FLOOR

    logger.warning("[ShortStrTurbDyn] _bid_price: %s bid=0 mark=0 — returning 0 (floor disabled, qty→1.0)", symbol)
    return 0.0


def _legs_factory(market_data):
    """
    Resolve option symbols and compute dynamic quantity from bid prices.
    Called by StrategyRunner._open_trade() before any orders are placed.

    Steps:
      1. Resolve concrete option symbols via delta/DTE criteria.
      2. Fetch bid prices for each resolved symbol (see _bid_price fallback ladder).
      3. Compute combined bid premium in USD.
      4. Size quantity: floor(DYN_TARGET_PREMIUM / premium_usd, step=0.1),
         capped at MAX_QUANTITY, minimum 0.1.
      5. Apply quantity to all legs and return.
    """
    # 1. Resolve symbols
    leg_specs = strangle(
        qty=1.0,
        call_delta=+DELTA,
        put_delta=-DELTA,
        dte=DTE,
        side="sell",
        min_otm_pct=MIN_OTM_PCT,
    )
    legs = resolve_legs(leg_specs, market_data)

    # 2. Fetch bid prices
    index_price = get_btc_index_price(use_cache=True) or 0.0
    total_bid_btc = sum(_bid_price(leg.symbol) for leg in legs)

    # 3. Compute dynamic quantity
    combined_premium_usd = total_bid_btc * index_price
    qty = _compute_quantity(combined_premium_usd)

    logger.info(
        "[ShortStrTurbDyn] legs_factory: qty=%.1fx "
        "(combined_bid=%.4f BTC = $%.0f, target=$%.0f, max=%.1f)",
        qty, total_bid_btc, combined_premium_usd, DYN_TARGET_PREMIUM, MAX_QUANTITY,
    )

    # 4. Apply quantity to all legs
    for leg in legs:
        leg.qty = qty

    return legs


# ─── Turbulence Gate ────────────────────────────────────────────────────────

def _turbulence_ok(dt: Optional[datetime] = None) -> bool:
    """
    Return True if the current turbulence composite score is below
    TURBULENCE_THRESHOLD, or if data is unavailable (fail-open).

    Uses the Binance 15m BTCUSDT klines (public, no auth required).
    Results are cached inside indicators/data.py for the interval TTL (~5min).
    """
    try:
        df_15m = fetch_klines(symbol="BTCUSDT", interval="15m", lookback_bars=1500)
        if df_15m is None or df_15m.empty:
            logger.warning("[TurbulenceGate] No kline data — failing open")
            return True

        df_turb = _compute_turbulence(df_15m)
        if df_turb is None or df_turb.empty:
            logger.warning("[TurbulenceGate] Indicator produced no output — failing open")
            return True

        if dt is None:
            dt = datetime.now(timezone.utc)

        # Look up the current hour's composite score
        hour_ts = dt.replace(minute=0, second=0, microsecond=0)
        if hour_ts not in df_turb.index:
            logger.debug("[TurbulenceGate] No row for %s — failing open", hour_ts)
            return True

        composite = df_turb.loc[hour_ts, "composite"]

        try:
            if math.isnan(float(composite)):
                logger.debug("[TurbulenceGate] composite=NaN (weekend/warmup) — failing open")
                return True
        except (TypeError, ValueError):
            return True

        score = float(composite)
        ok = score < TURBULENCE_THRESHOLD
        logger.debug(
            "[TurbulenceGate] composite=%.1f threshold=%.1f → %s",
            score, TURBULENCE_THRESHOLD, "OK" if ok else "BLOCKED"
        )
        return ok

    except Exception:
        logger.exception("[TurbulenceGate] Error evaluating turbulence — failing open")
        return True


# ─── Entry Condition: Turbulence-Gated Time Window ─────────────────────────

def _turbulence_entry():
    """
    Entry condition that combines a time window with a turbulence gate.

    Opens when ALL of:
      - current UTC hour >= ENTRY_HOUR  (watches until midnight — no hard cutoff)
      - turbulence composite < TURBULENCE_THRESHOLD  (fail-open on missing data)
    """
    label = (
        f"turbulence_entry(hour>={ENTRY_HOUR}, "
        f"threshold={TURBULENCE_THRESHOLD:.0f})"
    )

    def _check(account, trade=None):
        now = datetime.now(timezone.utc)
        hour = now.hour

        # Outside the watch window
        if hour < ENTRY_HOUR:
            return False

        if not _turbulence_ok(now):
            logger.info(
                "[TurbulenceGate] Turbulence above threshold (%.0f) at %02d:00 UTC — "
                "waiting for next tick", TURBULENCE_THRESHOLD, hour
            )
            return False

        return True

    _check.__name__ = label
    return _check


# ─── Fair Price Helper ──────────────────────────────────────────────────────

def _fair(symbol):
    # type: (str) -> Optional[dict]
    """
    Compute fair price for one option leg in fill_price-native units.

    On Deribit, fill_price is BTC-denominated.
    On Coincall, fill_price is USD.
    """
    details = get_option_details(symbol)
    if not details:
        return None

    if "_mark_price_btc" in details:
        bid  = float(details.get("_best_bid_btc",   0) or 0)
        ask  = float(details.get("_best_ask_btc",   0) or 0)
        mark = float(details.get("_mark_price_btc", 0) or 0)
    else:
        bid  = float(details.get("bid",       0) or 0)
        ask  = float(details.get("ask",       0) or 0)
        mark = float(details.get("markPrice", 0) or 0)

    if bid > 0 and ask > 0:
        fair = mark if bid <= mark <= ask else (bid + ask) / 2
    elif bid > 0:
        fair = max(mark, bid) if mark > 0 else bid
    elif ask > 0:
        fair = min(mark, ask) if mark > 0 else ask
    elif mark > 0:
        fair = mark
    else:
        return None

    index_price = float(details.get("indexPrice", 0) or 0)

    return {
        "fair":        fair,
        "bid":         bid  if bid  > 0 else None,
        "ask":         ask  if ask  > 0 else None,
        "mark":        mark,
        "index_price": index_price,
    }


# ─── Exit Condition: Combined Fair-Price Stop Loss ──────────────────────────

def _combined_sl():
    """
    Exit condition: combined fair-price based stop loss.

    Fires when (call_fair + put_fair) >= combined_fill_premium × (1 + STOP_LOSS_PCT).
    """
    label = f"combined_fair_sl({STOP_LOSS_PCT:.0%})"

    def _check(account, trade):
        open_legs = trade.open_legs
        if len(open_legs) < 2:
            return False

        call_leg = next((l for l in open_legs if l.symbol.endswith("-C")), None)
        put_leg  = next((l for l in open_legs if l.symbol.endswith("-P")), None)
        if not call_leg or not put_leg:
            return False

        if not call_leg.fill_price or not put_leg.fill_price:
            return False

        sl_threshold = trade.metadata.get("sl_threshold")
        if sl_threshold is None:
            combined_premium = float(call_leg.fill_price) + float(put_leg.fill_price)
            sl_threshold = combined_premium * (1.0 + STOP_LOSS_PCT)
            trade.metadata["sl_threshold"]     = sl_threshold
            trade.metadata["combined_premium"] = combined_premium

        call_fp = _fair(call_leg.symbol)
        put_fp  = _fair(put_leg.symbol)
        if not call_fp or not put_fp:
            return False

        combined_fair = call_fp["fair"] + put_fp["fair"]
        triggered = combined_fair >= sl_threshold

        if triggered:
            combined_premium = trade.metadata.get("combined_premium", 0)
            loss_pct = (combined_fair - combined_premium) / combined_premium * 100 if combined_premium else 0
            logger.info(
                f"[{trade.id}] {label} TRIGGERED: combined_fair={combined_fair:.4f} "
                f">= threshold={sl_threshold:.4f} "
                f"(premium={combined_premium:.4f}, loss={loss_pct:.1f}%)"
            )
            trade.execution_mode = "limit"
            trade.metadata["sl_triggered"]    = True
            trade.metadata["sl_triggered_at"] = time.time()

        return triggered

    _check.__name__ = label
    return _check


# ─── Exit Condition: Combined Ask-Price Take Profit ─────────────────────────

def _combined_tp():
    """
    Exit condition: combined ask-price based take profit.

    Fires when (premium - combined_ask) / premium >= TAKE_PROFIT_PCT.
    Uses raw ask prices — no mark/fair floor — matching backtester behaviour.
    """
    if TAKE_PROFIT_PCT <= 0:
        def _noop(account, trade):
            return False
        _noop.__name__ = "combined_ask_tp(disabled)"
        return _noop

    label = f"combined_ask_tp({TAKE_PROFIT_PCT:.0%})"

    def _check(account, trade):
        open_legs = trade.open_legs
        if len(open_legs) < 2:
            return False

        call_leg = next((l for l in open_legs if l.symbol.endswith("-C")), None)
        put_leg  = next((l for l in open_legs if l.symbol.endswith("-P")), None)
        if not call_leg or not put_leg:
            return False

        if not call_leg.fill_price or not put_leg.fill_price:
            return False

        combined_premium = trade.metadata.get("combined_premium")
        if combined_premium is None:
            combined_premium = float(call_leg.fill_price) + float(put_leg.fill_price)
            trade.metadata["combined_premium"] = combined_premium

        if combined_premium <= 0:
            return False

        call_fp = _fair(call_leg.symbol)
        put_fp  = _fair(put_leg.symbol)
        if not call_fp or not put_fp:
            return False

        call_ask = call_fp.get("ask")
        put_ask  = put_fp.get("ask")
        if not call_ask or not put_ask:
            return False

        combined_ask = call_ask + put_ask
        profit_ratio = (combined_premium - combined_ask) / combined_premium
        triggered = profit_ratio >= TAKE_PROFIT_PCT

        if triggered:
            logger.info(
                f"[{trade.id}] {label} TRIGGERED: combined_ask={combined_ask:.4f} "
                f"profit_ratio={profit_ratio:.2%} >= {TAKE_PROFIT_PCT:.0%} "
                f"(premium={combined_premium:.4f})"
            )
            trade.execution_mode = "limit"
            trade.metadata["tp_triggered"]    = True
            trade.metadata["tp_triggered_at"] = time.time()

        return triggered

    _check.__name__ = label
    return _check


# ─── Trade Callbacks ────────────────────────────────────────────────────────

def _structure_label():
    # type: () -> str
    delta_pct = int(round(DELTA * 100))
    return f"{delta_pct}Δ strangle ({DTE}DTE) [turb&lt;{TURBULENCE_THRESHOLD:.0f}]"


def _on_trade_opened(trade, account):
    index_price = get_btc_index_price(use_cache=False)
    if index_price is not None:
        trade.metadata["entry_index_price"] = index_price

    open_legs = trade.open_legs
    call_leg = next((l for l in open_legs if l.symbol.endswith("-C")), None)
    put_leg  = next((l for l in open_legs if l.symbol.endswith("-P")), None)

    call_fill = float(call_leg.fill_price) if call_leg and call_leg.fill_price else 0.0
    put_fill  = float(put_leg.fill_price)  if put_leg  and put_leg.fill_price  else 0.0
    combined_premium = call_fill + put_fill

    if combined_premium > 0:
        sl_threshold = combined_premium * (1.0 + STOP_LOSS_PCT)
        trade.metadata["sl_threshold"]     = sl_threshold
        trade.metadata["combined_premium"] = combined_premium

    # Read quantity determined by _legs_factory before open
    qty = float(open_legs[0].qty) if open_legs else 1.0
    trade.metadata["quantity"] = qty

    # Max-hold close uses a dedicated 1-phase aggressive profile
    _max_hold_profile = get_profile("max_hold_close_1phase")
    trade.metadata["_max_hold_close_profile"] = _max_hold_profile

    duration_s = int(trade.opened_at - trade.created_at) if trade.opened_at and trade.created_at else 0
    phase_label = "Phase 1 (at fair)" if duration_s <= LIMIT_OPEN_FAIR_SECONDS else "Phase 2 (at bid)"

    call_fp = _fair(call_leg.symbol) if call_leg else None
    put_fp  = _fair(put_leg.symbol)  if put_leg  else None
    call_fair = call_fp["fair"] if call_fp else 0.0
    put_fair  = put_fp["fair"]  if put_fp  else 0.0
    combined_fair = call_fair + put_fair

    vs_fair = (
        f"{(combined_premium - combined_fair) / combined_fair * 100:+.1f}% vs fair"
        if combined_fair > 0 else ""
    )

    structure = _structure_label()
    ts  = datetime.now(timezone.utc).strftime("%H:%M UTC")
    idx = index_price or 0.0

    logger.info(
        f"[ShortStrTurbDyn] Opened {structure}: "
        f"qty={qty}x  "
        f"CALL {call_leg.symbol if call_leg else '?'} @ {call_fill:.4f} | "
        f"PUT  {put_leg.symbol  if put_leg  else '?'} @ {put_fill:.4f} | "
        f"combined={combined_premium:.4f}  SL@={trade.metadata.get('sl_threshold', 0):.4f}  "
        f"BTC=${idx:,.0f}  {phase_label} {duration_s}s"
    )

    sl_thresh  = trade.metadata.get("sl_threshold", 0)
    hold_label = f"{MAX_HOLD_HOURS}h" if MAX_HOLD_HOURS > 0 else "expiry"
    tp_label   = f"{TAKE_PROFIT_PCT:.0%}" if TAKE_PROFIT_PCT > 0 else "off"
    otm_label  = f"{MIN_OTM_PCT:.1f}%" if MIN_OTM_PCT > 0 else "off"
    try:
        get_notifier().send(
            f"📉 <b>Short {structure.title()} — Trade Opened</b>\n\n"
            f"Time: {ts}  |  BTC: ${idx:,.0f}\n"
            f"ID: {trade.id}\n\n"
            f"SELL {qty}× {call_leg.symbol if call_leg else '?'}  @ {call_fill:.4f} BTC (${call_fill * idx:,.2f})\n"
            f"SELL {qty}× {put_leg.symbol  if put_leg  else '?'}  @ {put_fill:.4f} BTC (${put_fill * idx:,.2f})\n\n"
            f"Combined premium: <b>{combined_premium:.4f} BTC</b> (${combined_premium * idx:,.2f})  ({vs_fair})\n"
            f"Combined premium (×{qty}): <b>${combined_premium * idx * qty:,.2f}</b>\n"
            f"SL threshold: {sl_thresh:.4f} BTC (${sl_thresh * idx:,.2f})  (+{STOP_LOSS_PCT:.0%})\n"
            f"TP: {tp_label}  |  Min OTM: {otm_label}  |  Turb&lt;{TURBULENCE_THRESHOLD:.0f}\n"
            f"Max hold: {hold_label}  |  {phase_label}  {duration_s}s\n\n"
            f"Fair call: {call_fair:.4f} BTC  |  Fair put: {put_fair:.4f} BTC  |  "
            f"Fair combined: {combined_fair:.4f} BTC (${combined_fair * idx:,.2f})\n"
            f"Equity: ${account.equity:,.2f}  |  Avail: ${account.available_margin:,.2f}"
        )
    except Exception:
        pass


def _on_trade_closed(trade, account):
    idx   = get_btc_index_price(use_cache=False) or 0.0
    pnl   = trade.realized_pnl if trade.realized_pnl is not None else 0.0
    pnl_usd = pnl * idx
    combined_premium = trade.metadata.get("combined_premium", 0.0)
    qty = trade.metadata.get("quantity", 1.0)
    roi = (pnl / abs(combined_premium) * 100) if combined_premium else 0.0
    hold_seconds = trade.hold_seconds or 0

    if trade.metadata.get("tp_triggered"):
        exit_label = f"Take Profit ({TAKE_PROFIT_PCT:.0%})"
    elif trade.metadata.get("sl_triggered"):
        exit_label = f"Stop Loss ({STOP_LOSS_PCT:.0%})"
    elif MAX_HOLD_HOURS > 0 and hold_seconds >= MAX_HOLD_HOURS * 3600:
        exit_label = f"Max Hold ({MAX_HOLD_HOURS}h)"
    elif trade.metadata.get("expiry_settled"):
        exit_label = "Expiry"
    else:
        exit_label = "closed"

    logger.info(
        f"[ShortStrTurbDyn] Closed: {trade.id}  |  qty={qty}x  |  PnL: ${pnl_usd:+.2f}  |  "
        f"ROI: {roi:+.1f}%  |  Hold: {hold_seconds / 60:.1f}min  |  Exit: {exit_label}"
    )

    ts    = datetime.now(timezone.utc).strftime("%H:%M UTC")
    emoji = "✅" if pnl >= 0 else "❌"

    open_legs  = trade.open_legs
    call_open  = next((l for l in open_legs if l.symbol.endswith("-C")), None)
    put_open   = next((l for l in open_legs if l.symbol.endswith("-P")), None)
    call_fill_open = float(call_open.fill_price) if call_open and call_open.fill_price else 0.0
    put_fill_open  = float(put_open.fill_price)  if put_open  and put_open.fill_price  else 0.0

    close_legs      = trade.close_legs or []
    call_close      = next((l for l in close_legs if l.symbol.endswith("-C")), None)
    put_close       = next((l for l in close_legs if l.symbol.endswith("-P")), None)
    call_fill_close = float(call_close.fill_price) if call_close and call_close.fill_price else 0.0
    put_fill_close  = float(put_close.fill_price)  if put_close  and put_close.fill_price  else 0.0
    combined_close  = call_fill_close + put_fill_close

    call_sym = call_close.symbol if call_close else (call_open.symbol if call_open else None)
    put_sym  = put_close.symbol  if put_close  else (put_open.symbol  if put_open  else None)
    call_fp  = _fair(call_sym) if call_sym else None
    put_fp   = _fair(put_sym)  if put_sym  else None
    combined_fair_now = (
        (call_fp["fair"] if call_fp else 0.0) + (put_fp["fair"] if put_fp else 0.0)
    )

    structure  = _structure_label()
    open_fees  = float(trade.open_fees)  if trade.open_fees  else 0.0
    close_fees = float(trade.close_fees) if trade.close_fees else 0.0
    total_fees = open_fees + close_fees
    net_pnl    = pnl - total_fees
    net_pnl_usd = net_pnl * idx

    try:
        detail_line = ""
        if trade.metadata.get("tp_triggered"):
            detail_line = (
                f"TP target: {TAKE_PROFIT_PCT:.0%} of premium  |  "
                f"Fair now: {combined_fair_now:.4f} BTC (${combined_fair_now * idx:,.2f})\n"
            )
        elif trade.metadata.get("sl_triggered"):
            sl_thresh = trade.metadata.get("sl_threshold", 0)
            detail_line = (
                f"SL threshold: {sl_thresh:.4f} BTC (${sl_thresh * idx:,.2f})  |  "
                f"Fair now: {combined_fair_now:.4f} BTC (${combined_fair_now * idx:,.2f})\n"
            )

        fee_line = ""
        if total_fees > 0:
            fee_line = (
                f"\nFees: {total_fees:.6f} BTC (${total_fees * idx:,.2f})  "
                f"[open {open_fees:.6f} + close {close_fees:.6f}]\n"
            )

        get_notifier().send(
            f"{emoji} <b>Short {structure.title()} — Trade Closed</b>\n\n"
            f"Time: {ts}  |  BTC: ${idx:,.0f}\n"
            f"ID: {trade.id}  |  Hold: {hold_seconds / 60:.1f} min\n\n"
            f"Trigger: <b>{exit_label}</b>\n"
            f"{detail_line}"
            f"\nOpen:  {qty}× CALL {call_fill_open:.4f} BTC  PUT {put_fill_open:.4f} BTC  "
            f"→  {combined_premium:.4f} BTC (${combined_premium * idx:,.2f})\n"
            f"Close: {qty}× CALL {call_fill_close:.4f} BTC  PUT {put_fill_close:.4f} BTC  "
            f"→  {combined_close:.4f} BTC (${combined_close * idx:,.2f})\n"
            f"{fee_line}"
            f"\nGross PnL: ${pnl_usd:+.2f}  ({roi:+.1f}%)\n"
            f"Net PnL: <b>${net_pnl_usd:+.2f}</b>\n"
            f"Equity: ${account.equity:,.2f}"
        )
    except Exception:
        pass


# ─── Exit Condition: Max Hold with Profile Override ─────────────────────────

def _max_hold_close():
    """
    Exit condition: max hold timer with close profile override.

    Swaps execution profile to max_hold_close_1phase (single aggressive phase).
    """
    if MAX_HOLD_HOURS <= 0:
        def _noop(account, trade):
            return False
        _noop.__name__ = "max_hold_close(disabled)"
        return _noop

    label = f"max_hold_close({MAX_HOLD_HOURS}h)"

    def _check(account, trade):
        hold = trade.hold_seconds
        if hold is None:
            return False
        triggered = hold >= MAX_HOLD_HOURS * 3600
        if triggered:
            logger.info(f"[{trade.id}] {label} triggered: held {hold/3600:.1f}h")
            max_hold_profile = trade.metadata.get("_max_hold_close_profile")
            if max_hold_profile:
                trade.metadata["_execution_profile"] = max_hold_profile
        return triggered

    _check.__name__ = label
    return _check


# ─── Strategy Factory ────────────────────────────────────────────────────────

def short_str_turb_dyn() -> StrategyConfig:
    """
    Short N-DTE strangle selected by delta, gated by Turbulence indicator,
    with dynamic contract-quantity sizing based on a USD premium target.

    Entry fires from entry_hour UTC until midnight when turbulence composite
    is below TURBULENCE_THRESHOLD.
    Exits on SL, optional TP, optional max-hold, or expiry.
    Quantity per leg is sized dynamically: round(min(DYN_TARGET_PREMIUM / premium_usd, MAX_QUANTITY), 1).
    """
    exit_conditions = [_combined_tp(), _combined_sl()]
    if MAX_HOLD_HOURS > 0:
        exit_conditions.append(_max_hold_close())

    return StrategyConfig(
        name="short_str_turb_dyn",

        # ── What to trade ─────────────────────────────────────────────
        # legs_factory resolves symbols + computes dynamic qty before open.
        # The static legs= below acts as documentation of the structure only.
        legs=strangle(
            qty=1.0,
            call_delta=+DELTA,
            put_delta=-DELTA,
            dte=DTE,
            side="sell",
            min_otm_pct=MIN_OTM_PCT,
        ),
        legs_factory=_legs_factory,

        # ── When to enter ─────────────────────────────────────────────
        entry_conditions=[
            _turbulence_entry(),
            *([
                weekday_filter(["mon", "tue", "wed", "thu", "fri"])
            ] if WEEKEND_FILTER else []),
        ],

        # ── When to exit ──────────────────────────────────────────────
        exit_conditions=exit_conditions,

        # ── How to execute ────────────────────────────────────────────
        execution_mode="limit",
        execution_profile="strangle_turb_best_effort",

        rfq_params=RFQParams(
            timeout_seconds=15,
            min_improvement_pct=-999,
            fallback_mode="limit",
        ),

        # ── Operational limits ────────────────────────────────────────
        max_concurrent_trades=MAX_CONCURRENT,
        max_trades_per_day=1,
        cooldown_seconds=0,
        check_interval_seconds=CHECK_INTERVAL,

        # ── Callbacks ─────────────────────────────────────────────────
        on_trade_opened=_on_trade_opened,
        on_trade_closed=_on_trade_closed,
    )
