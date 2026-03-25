"""
Daily Put Sell — BTC 1DTE OTM Put Selling Strategy (v2)

Sells a 1DTE BTC put option near -0.10 delta every day during 03:00–04:00 UTC.

Fair Price Model:
  At 3 AM the orderbook for far-OTM options is thin.  The exchange mark
  price can be far from tradeable reality.  We compute a "fair price":
    - mark, if it sits between bid and ask (mark is reasonable)
    - mid = (bid+ask)/2, if mark is outside the spread (mark is stale/off)
    - max(mark, bid), if only bid exists (no ask side)
    - mark alone, if the book is empty (last resort)
  fairspread = fair - bid  (measures how far bid is from fair value)

Open Execution (sell put — patient, up to ~5 min total):
  Phase 1 — RFQ (20s silent + up to 3 min gated):
    Collect market-maker quotes for 20s, then accept if the quote is
    at least bid + 33% of fairspread.  MMs rarely beat the book at 3 AM,
    so this often times out — but costs nothing to try.
  Phase 2.1 — Limit at fair (60s):
    Place limit sell at our computed fair price.
  Phase 2.2 — Limit at bid + 33% of spread (60s):
    Step closer to bid — one third of the way from bid to fair.
    Skipped if computed price < fair × (1 − MIN_BID_DISCOUNT_PCT%).
  Phase 2.3 — Limit at bid (60s):
    Hit the bid — aggressive fill to ensure entry.
    Skipped if bid < fair × (1 − MIN_BID_DISCOUNT_PCT%).

Minimum Fill Price (liquidity guard):
  MIN_BID_DISCOUNT_PCT controls the worst acceptable sell price relative to
  fair value.  Default 17%: we won't sell below fair × 0.83.  In thin weekend
  or overnight markets where bid is far from fair, phases 2.2 and 2.3 will
  refuse to place and the trade simply does not open.  The stop loss bypasses
  this check — once in a trade we close no matter what.

Stop Loss (fair-price based, 70% of premium):
  SL threshold = fill_price × 1.7 (70% loss).  Each tick, we recompute
  fair price.  If fair_price >= SL threshold, close via phased limit
  buy-to-close: 15s at fair → 15s stepping toward ask → aggressive at ask.
  Skips RFQ entirely for fast execution on SL.

Expiry:
  If SL does not fire, the option expires worthless (full win).
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from ema_filter import ema20_filter
from market_data import get_btc_index_price, get_option_market_data
from option_selection import LegSpec
from strategy import (
    StrategyConfig,
    # Entry conditions
    time_window,
    min_available_margin_pct,
)
from trade_execution import ExecutionParams, ExecutionPhase
from trade_lifecycle import RFQParams
from telegram_notifier import get_notifier

logger = logging.getLogger(__name__)


# ─── Strategy Parameters ────────────────────────────────────────────────────
# Overridable via PARAM_* env vars (set by slot .toml config at deploy time).
# Defaults below match the current production values.

import os as _os
def _p(name, default, cast=float):
    """Read PARAM_<NAME> from env, falling back to default."""
    return cast(_os.getenv(f"PARAM_{name}", str(default)))

# Structure
QTY = _p("QTY", 0.8)                             # BTC per leg (~$68k notional)
TARGET_DELTA = _p("TARGET_DELTA", -0.10)          # OTM put delta target
DTE = _p("DTE", 1, int)                           # 1 day to expiry

# Scheduling — UTC hours
ENTRY_HOUR_START = _p("ENTRY_HOUR_START", 3, int) # Open window: 03:00 UTC
ENTRY_HOUR_END = _p("ENTRY_HOUR_END", 4, int)     # Close window: 04:00 UTC

# Risk
MIN_MARGIN_PCT = _p("MIN_MARGIN_PCT", 10, int)    # Require ≥10% available margin
STOP_LOSS_PCT = _p("STOP_LOSS_PCT", 70, int)      # 70% loss of premium collected

# RFQ open — phased execution
RFQ_OPEN_TIMEOUT = _p("RFQ_OPEN_TIMEOUT", 200, int)         # 20s silent + 180s (3 min) gated window
RFQ_INITIAL_WAIT = _p("RFQ_INITIAL_WAIT", 20, int)          # Collect quotes silently for 20s
RFQ_SPREAD_FRACTION = _p("RFQ_SPREAD_FRACTION", 0.33)       # Accept if quote ≥ bid + 33% of fairspread

# Limit open fallback — phased after RFQ timeout
LIMIT_OPEN_FAIR_SECONDS = _p("LIMIT_OPEN_FAIR_SECONDS", 60, int)       # Phase 2.1: quote at fair price
LIMIT_OPEN_PARTIAL_SECONDS = _p("LIMIT_OPEN_PARTIAL_SECONDS", 60, int) # Phase 2.2: quote at bid + 33% fairspread
LIMIT_OPEN_BID_SECONDS = _p("LIMIT_OPEN_BID_SECONDS", 60, int)         # Phase 2.3: aggressive at bid

# Liquidity guard — minimum acceptable open fill price
MIN_BID_DISCOUNT_PCT = _p("MIN_BID_DISCOUNT_PCT", 17)          # Won't sell below fair × (1 - %/100)

# SL close — phased limit buy-to-close (no RFQ)
SL_CLOSE_FAIR_SECONDS = _p("SL_CLOSE_FAIR_SECONDS", 15, int)   # Buy at fair price
SL_CLOSE_STEP_SECONDS = _p("SL_CLOSE_STEP_SECONDS", 15, int)   # Step toward ask
SL_CLOSE_AGG_SECONDS = _p("SL_CLOSE_AGG_SECONDS", 60, int)     # Aggressive at ask

# Operational
CHECK_INTERVAL = _p("CHECK_INTERVAL", 15, int)    # Seconds between entry/exit evaluations
MAX_CONCURRENT = _p("MAX_CONCURRENT", 2, int)      # Allow 2 overlapping trades (expiry overlap)


# ─── Fair Price Calculation ─────────────────────────────────────────────────
# At 3 AM, far-OTM option books are thin.  Exchange mark can diverge wildly
# from what's actually tradeable.  This function computes a "fair" price by
# cross-referencing mark against the orderbook:
#   - mark between bid and ask  →  trust mark
#   - mark outside bid/ask      →  use mid = (bid+ask)/2
#   - only bid exists            →  max(mark, bid)
#   - no book at all             →  mark (last resort)
#
# fairspread = fair - bid  measures the gap between bid and fair value.
# fairspread_ask = ask - fair  measures the gap on the ask side.

def compute_fair_price(symbol: str) -> Optional[dict]:
    """
    Compute fair price, bid, ask, and spreads for an option symbol.

    Returns dict with keys: fair, bid, ask, mark, fairspread, fairspread_ask.
    Returns None if no market data is available at all.
    """
    mkt = get_option_market_data(symbol)
    if not mkt:
        return None

    bid = float(mkt.get('bid', 0) or 0)
    ask = float(mkt.get('ask', 0) or 0)
    mark = float(mkt.get('mark_price', 0) or 0)

    if bid > 0 and ask > 0:
        # Both sides of the book exist — best case
        if bid <= mark <= ask:
            fair = mark
        else:
            fair = (bid + ask) / 2
        fairspread = fair - bid
        fairspread_ask = ask - fair
    elif bid > 0:
        # Only bid side — no ask in book
        fair = max(mark, bid) if mark > 0 else bid
        fairspread = fair - bid
        fairspread_ask = 0.0
    elif mark > 0:
        # No book at all — use mark as last resort
        fair = mark
        fairspread = 0.0
        fairspread_ask = 0.0
    else:
        return None

    return {
        'fair': fair,
        'bid': bid if bid > 0 else None,
        'ask': ask if ask > 0 else None,
        'mark': mark,
        'fairspread': fairspread,
        'fairspread_ask': fairspread_ask,
    }


# ─── Dynamic RFQ Gate ──────────────────────────────────────────────────────
# The RFQ improvement gate is a percentage: how much better than the orderbook
# bid the quote needs to be.  We compute this dynamically at trade time from
# the fair price model:  quote ≥ bid + 33% * fairspread  ↔  improvement ≥ X%.

def _compute_rfq_gate(trade) -> float:
    """
    Callable for metadata['rfq_min_book_improvement_pct'].

    Called by execution_router just before submitting the RFQ.
    Computes the improvement % threshold from the current fair price.
    """
    if not trade.open_legs:
        return 999.0

    symbol = trade.open_legs[0].symbol
    fp = compute_fair_price(symbol)

    if not fp or not fp['bid'] or fp['fairspread'] <= 0:
        logger.warning(
            f"[DailyPutSell] No bid or zero fairspread for {symbol} "
            f"— RFQ gate set high (will likely time out)"
        )
        return 999.0

    # Improvement = (quote - bid) / bid × 100
    # We want quote ≥ bid + fraction × fairspread
    # So: min_improvement = fraction × fairspread / bid × 100
    gate_pct = RFQ_SPREAD_FRACTION * fp['fairspread'] / fp['bid'] * 100
    logger.info(
        f"[DailyPutSell] RFQ gate: bid=${fp['bid']:.2f} fair=${fp['fair']:.2f} "
        f"spread=${fp['fairspread']:.2f} → min_improvement={gate_pct:.1f}%"
    )
    return gate_pct


# ─── Exit Condition: Fair-Price Stop Loss ───────────────────────────────────
# For a short put, we lose money when the option price RISES (underlying drops,
# put goes ITM).  SL fires when the current fair price reaches 1.7× fill price,
# meaning we'd lose 70% of the premium we collected.
#
# On trigger, this condition also configures the close execution: switches from
# RFQ to limit mode with phased pricing (fair → step toward ask → aggressive).

def _fair_price_sl():
    """
    Exit condition: fair-price based stop loss.

    SL threshold = fill_price × (1 + STOP_LOSS_PCT/100).
    Triggers when fair_price ≥ SL threshold.

    On trigger, configures phased limit close (buy-to-close) and sets
    execution_mode to 'limit' so the close bypasses RFQ.
    """
    label = f"fair_price_sl({STOP_LOSS_PCT}%)"

    def _check(account, trade) -> bool:
        leg = trade.open_legs[0] if trade.open_legs else None
        if not leg or not leg.fill_price:
            return False

        # Compute or retrieve SL threshold (set once, stored in metadata)
        sl_threshold = trade.metadata.get("sl_threshold")
        if sl_threshold is None:
            sl_threshold = float(leg.fill_price) * (1.0 + STOP_LOSS_PCT / 100.0)
            trade.metadata["sl_threshold"] = sl_threshold

        # Get current fair price
        fp = compute_fair_price(leg.symbol)
        if not fp or fp['fair'] <= 0:
            return False  # no data — skip this tick (safe)

        triggered = fp['fair'] >= sl_threshold
        if triggered:
            loss_pct = (fp['fair'] - float(leg.fill_price)) / float(leg.fill_price) * 100
            logger.info(
                f"[{trade.id}] {label} TRIGGERED: fair=${fp['fair']:.2f} "
                f">= threshold=${sl_threshold:.2f} "
                f"(fill=${leg.fill_price:.2f}, loss={loss_pct:.1f}%)"
            )
            logger.info(
                f"[{trade.id}] SL prices: "
                f"bid=${fp['bid'] or 0:.2f}  ask=${fp['ask'] or 0:.2f}  "
                f"mid=${((fp['bid'] or 0) + (fp['ask'] or 0)) / 2:.2f}  "
                f"fair=${fp['fair']:.2f}  mark=${fp['mark']:.2f}  "
                f"fairspread=${fp['fairspread']:.2f}"
            )

            # Configure phased limit close (buy-to-close, no RFQ)
            trade.execution_mode = "limit"
            trade.metadata["sl_triggered"] = True
            trade.metadata["sl_triggered_at"] = time.time()
            trade.execution_params = ExecutionParams(phases=[
                # Phase 1: buy at fair price (passive)
                ExecutionPhase(
                    pricing="fair", fair_aggression=0.0,
                    duration_seconds=SL_CLOSE_FAIR_SECONDS,
                    reprice_interval=SL_CLOSE_FAIR_SECONDS,
                ),
                # Phase 2: step toward ask (more aggressive)
                ExecutionPhase(
                    pricing="fair", fair_aggression=0.33,
                    duration_seconds=SL_CLOSE_STEP_SECONDS,
                    reprice_interval=SL_CLOSE_STEP_SECONDS,
                ),
                # Phase 3: aggressive at ask (or mark×1.2 if no ask)
                ExecutionPhase(
                    pricing="fair", fair_aggression=1.0,
                    duration_seconds=SL_CLOSE_AGG_SECONDS,
                    reprice_interval=15,
                ),
            ])

        return triggered

    _check.__name__ = label
    return _check


# ─── Trade Callbacks ────────────────────────────────────────────────────────

def _on_trade_opened(trade, account) -> None:
    """
    Called when the short put trade is opened (RFQ or limit filled).

    1. Computes fair price and SL threshold from fill price.
    2. Logs entry details and sends Telegram notification.
    """
    # Capture entry index price
    index_price = get_btc_index_price(use_cache=False)
    if index_price is not None:
        trade.metadata["entry_index_price"] = index_price

    leg = trade.open_legs[0] if trade.open_legs else None
    premium = leg.fill_price if leg and leg.fill_price else 0

    # Compute fair price at open and SL threshold
    fair_at_open = None
    if leg:
        fp = compute_fair_price(leg.symbol)
        if fp:
            fair_at_open = fp['fair']
            trade.metadata["fair_at_open"] = fair_at_open
            trade.metadata["bid_at_open"] = fp['bid']
            trade.metadata["ask_at_open"] = fp['ask']
            trade.metadata["fairspread_at_open"] = fp['fairspread']

    # SL threshold: fill_price × 1.7 for 70% loss
    sl_threshold = None
    if premium and premium > 0:
        sl_threshold = float(premium) * (1.0 + STOP_LOSS_PCT / 100.0)
        trade.metadata["sl_threshold"] = sl_threshold

    # Mark price at open (may already be in metadata from execution_router)
    mark_at_open = None
    if leg:
        mark_at_open = trade.metadata.get(f"mark_at_open_{leg.symbol}")
        if not mark_at_open:
            mkt = get_option_market_data(leg.symbol)
            if mkt:
                mark_at_open = mkt.get('mark_price', 0)
                trade.metadata[f"mark_at_open_{leg.symbol}"] = mark_at_open

    logger.info(
        f"[DailyPutSell] Opened: SELL {leg.symbol if leg else '?'} "
        f"@ ${premium:.4f}  |  fair=${fair_at_open:.4f}  |  "
        f"mark=${mark_at_open:.4f}  |  "
        f"SL@=${sl_threshold:.4f}  |  BTC=${index_price:,.0f}"
        if (fair_at_open and mark_at_open and sl_threshold and index_price)
        else
        f"[DailyPutSell] Opened: SELL {leg.symbol if leg else '?'} "
        f"@ ${premium:.4f}"
    )

    # Log detailed pricing snapshot at entry
    if leg:
        fp = compute_fair_price(leg.symbol)
        if fp:
            logger.info(
                f"[DailyPutSell] Entry prices: "
                f"bid=${fp['bid'] or 0:.2f}  ask=${fp['ask'] or 0:.2f}  "
                f"mid=${((fp['bid'] or 0) + (fp['ask'] or 0)) / 2:.2f}  "
                f"fair=${fp['fair']:.2f}  mark=${fp['mark']:.2f}  "
                f"fairspread=${fp['fairspread']:.2f}"
            )

    # Telegram notification
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")

    # Execution mode: RFQ result message or "Limit"
    exec_mode = "unknown"
    if trade.rfq_result and trade.rfq_result.success:
        exec_mode = trade.rfq_result.message or "RFQ"
    elif trade.rfq_result and not trade.rfq_result.success:
        exec_mode = f"Limit (RFQ failed: {trade.rfq_result.message})"
    else:
        exec_mode = "Limit"

    # Opening duration
    duration_s = int(trade.opened_at - trade.created_at) if trade.opened_at and trade.created_at else 0

    # Append limit phase to exec_mode (inferred from total open duration vs phase schedule)
    if exec_mode.startswith("Limit") and duration_s > 0:
        limit_elapsed = duration_s - RFQ_OPEN_TIMEOUT
        if limit_elapsed < LIMIT_OPEN_FAIR_SECONDS:
            exec_mode += " — Phase 2.1 (at fair)"
        elif limit_elapsed < LIMIT_OPEN_FAIR_SECONDS + LIMIT_OPEN_PARTIAL_SECONDS:
            exec_mode += " — Phase 2.2 (stepped)"
        else:
            exec_mode += " — Phase 2.3 (at bid)"

    # Price block
    bid = fp['bid'] or 0 if fp else 0
    ask = fp['ask'] or 0 if fp else 0
    mid = (bid + ask) / 2 if (bid and ask) else 0

    # Fill vs fair
    fill_vs_fair = ""
    if fair_at_open and premium:
        diff = premium - fair_at_open
        diff_pct = diff / fair_at_open * 100
        fill_vs_fair = f"Fill vs fair: ${premium:.2f} vs ${fair_at_open:.2f} ({diff_pct:+.1f}%)"

    try:
        get_notifier().send(
            f"📉 <b>Daily Put Sell — Trade Opened</b>\n\n"
            f"Time: {ts}\n"
            f"ID: {trade.id}\n"
            f"SELL {leg.filled_qty}× {leg.symbol}\n\n"
            f"Premium: <b>${premium:.2f}</b>\n"
            f"Execution: {exec_mode}\n"
            f"Duration: {duration_s}s\n\n"
            f"Prices at open:\n"
            f"  mark=${mark_at_open or 0:.2f}  mid=${mid:.2f}  fair=${fair_at_open or 0:.2f}\n"
            f"  bid=${bid:.2f}  ask=${ask:.2f}\n"
            f"{fill_vs_fair}\n\n"
            f"BTC index: ${index_price:,.0f}" if index_price else "BTC index: N/A"
        )
    except Exception:
        pass


def _on_trade_closed(trade, account) -> None:
    """
    Called when the short put trade is closed (SL or expiry).

    1. Logs PnL and close details.
    2. Sends Telegram notification.
    """
    pnl = trade.realized_pnl if trade.realized_pnl is not None else 0.0
    entry_cost = trade.total_entry_cost()
    roi = (pnl / abs(entry_cost) * 100) if entry_cost else 0.0
    hold_seconds = trade.hold_seconds or 0

    # Determine exit reason — priority: metadata flags > PnL > hold time
    exit_reason = "unknown"
    if trade.metadata.get("sl_triggered"):
        exit_reason = f"SL ({STOP_LOSS_PCT}% loss, fair-price)"
    elif pnl <= -(abs(entry_cost) * STOP_LOSS_PCT / 100):
        exit_reason = f"SL ({STOP_LOSS_PCT}% loss)"
    elif pnl > 0:
        exit_reason = "profit"
    elif hold_seconds > 82800 and abs(pnl) < abs(entry_cost) * 0.05:
        exit_reason = "expiry (worthless)"

    logger.info(
        f"[DailyPutSell] Closed: {trade.id}  |  PnL: ${pnl:+.4f}  |  "
        f"ROI: {roi:+.1f}%  |  Hold: {hold_seconds/60:.1f}min  |  "
        f"Exit: {exit_reason}"
    )

    # Telegram notification
    ts = datetime.now(timezone.utc).strftime("%H:%M UTC")
    emoji = "✅" if pnl >= 0 else "❌"

    # Trigger details
    leg = trade.open_legs[0] if trade.open_legs else None
    entry_price = float(leg.fill_price) if leg and leg.fill_price else 0
    sl_threshold = trade.metadata.get("sl_threshold")

    trigger_text = ""
    if trade.metadata.get("sl_triggered"):
        trigger_text = (
            f"Trigger: <b>Stop Loss</b>\n"
            f"SL threshold: ${sl_threshold:.2f} ({STOP_LOSS_PCT}% loss on ${entry_price:.2f} entry)"
            if sl_threshold else "Trigger: <b>Stop Loss</b>"
        )
    elif exit_reason.startswith("expiry"):
        trigger_text = "Trigger: <b>Expiry</b> (option expired worthless)"
    else:
        trigger_text = f"Trigger: <b>{exit_reason}</b>"

    # Price snapshot at close
    close_fill = None
    close_symbol = None
    if trade.close_legs and trade.close_legs[0].fill_price:
        close_fill = float(trade.close_legs[0].fill_price)
        close_symbol = trade.close_legs[0].symbol
    elif leg:
        close_symbol = leg.symbol

    fp = compute_fair_price(close_symbol) if close_symbol else None
    price_text = ""
    if fp:
        c_bid = fp['bid'] or 0
        c_ask = fp['ask'] or 0
        c_mid = (c_bid + c_ask) / 2 if (c_bid and c_ask) else 0
        price_text = (
            f"\nPrices at close:\n"
            f"  mark=${fp['mark']:.2f}  mid=${c_mid:.2f}  fair=${fp['fair']:.2f}\n"
            f"  bid=${c_bid:.2f}  ask=${c_ask:.2f}"
        )

    # Fill vs fair at close
    fill_vs_fair = ""
    if close_fill and fp and fp['fair'] > 0:
        diff = close_fill - fp['fair']
        diff_pct = diff / fp['fair'] * 100
        fill_vs_fair = f"\nFill vs fair: ${close_fill:.2f} vs ${fp['fair']:.2f} ({diff_pct:+.1f}%)"

    # Close execution phase (inferred from metadata and timing)
    close_qty = (trade.close_legs[0].filled_qty if (trade.close_legs and trade.close_legs[0].filled_qty) else None) or (leg.filled_qty if leg else '?')
    close_exec = ""
    if trade.metadata.get("sl_triggered"):
        sl_triggered_at = trade.metadata.get("sl_triggered_at")
        if sl_triggered_at and trade.closed_at:
            close_duration = trade.closed_at - float(sl_triggered_at)
            if close_duration < SL_CLOSE_FAIR_SECONDS:
                close_exec = "Execution: SL close — Phase 1 (at fair)"
            elif close_duration < SL_CLOSE_FAIR_SECONDS + SL_CLOSE_STEP_SECONDS:
                close_exec = "Execution: SL close — Phase 2 (stepped)"
            else:
                close_exec = "Execution: SL close — Phase 3 (at ask)"
        else:
            close_exec = "Execution: SL close (phased limit)"
    elif exit_reason.startswith("expiry"):
        close_exec = "Execution: expired"

    # BTC index
    close_index = get_btc_index_price(use_cache=False)
    idx_text = f"BTC index: ${close_index:,.0f}" if close_index else "BTC index: N/A"

    try:
        close_exec_line = f"{close_exec}\n" if close_exec else ""
        get_notifier().send(
            f"{emoji} <b>Daily Put Sell — Trade Closed</b>\n\n"
            f"Time: {ts}\n"
            f"ID: {trade.id}\n"
            f"BUY {close_qty}\u00d7 {close_symbol}\n"
            f"{trigger_text}\n"
            f"{close_exec_line}"
            f"\nPnL: <b>${pnl:+.2f}</b> ({roi:+.1f}%)\n"
            f"Hold: {hold_seconds/60:.1f} min\n"
            f"{price_text}"
            f"{fill_vs_fair}\n\n"
            f"{idx_text}"
        )
    except Exception:
        pass


# ─── Strategy Factory ──────────────────────────────────────────────────────

def daily_put_sell() -> StrategyConfig:
    """
    Daily BTC put selling strategy (v2).

    Sells a 1DTE OTM put (~10 delta) daily during 03:00–04:00 UTC.
    Uses fair-price model for execution and risk management.
    """
    return StrategyConfig(
        name="daily_put_sell",

        # ── What to trade ────────────────────────────────────────────
        legs=[
            LegSpec(
                option_type="P",
                side="sell",
                qty=QTY,
                strike_criteria={"type": "delta", "value": TARGET_DELTA},
                expiry_criteria={"dte": DTE},
            ),
        ],

        # ── When to enter ────────────────────────────────────────────
        entry_conditions=[
            time_window(ENTRY_HOUR_START, ENTRY_HOUR_END),
            # ema20_filter(),                       # TEST: disabled
            # min_available_margin_pct(MIN_MARGIN_PCT),  # TEST: disabled
        ],

        # ── When to exit ─────────────────────────────────────────────
        # SL: _fair_price_sl at 70% loss based on fair price vs fill.
        #     On trigger, switches to limit mode and configures phased
        #     buy-to-close (15s fair → 15s step → aggressive at ask).
        # Expiry: option expires worthless → full premium captured.
        exit_conditions=[
            _fair_price_sl(),
        ],

        # ── How to execute ───────────────────────────────────────────
        # OPEN path: RFQ phased → limit phased fallback.
        #   RFQ: 20s silent + 3 min gated (bid + 33% fairspread).
        #   Limit fallback: 60s at fair → 60s at bid+33%spread → 60s at bid.
        # CLOSE path: Configured dynamically by _fair_price_sl when SL fires
        #   (limit phased, no RFQ). For non-SL closes (manual/emergency),
        #   rfq_params provides a reasonable RFQ close as fallback.
        execution_mode="rfq",
        rfq_action="sell",

        # rfq_params: used for non-SL close paths (manual close, emergencies)
        rfq_params=RFQParams(
            timeout_seconds=15,
            min_improvement_pct=-999,    # accept any quote for emergency close
            fallback_mode="limit",
        ),

        # execution_params: used for limit open fallback (after RFQ timeout)
        # fair pricing with aggression 0→0.67→1.0 steps from fair→spread→bid
        execution_params=ExecutionParams(phases=[
            # Phase 2.1: sell at fair price — reprice_interval=duration so no mid-phase drift
            ExecutionPhase(
                pricing="fair", fair_aggression=0.0,
                duration_seconds=LIMIT_OPEN_FAIR_SECONDS,
                reprice_interval=LIMIT_OPEN_FAIR_SECONDS,
            ),
            # Phase 2.2: sell at bid + 33% of fairspread — reprice_interval=duration so no mid-phase drift
            # min_price_pct_of_fair: refuse to place if computed price < fair × floor
            ExecutionPhase(
                pricing="fair", fair_aggression=0.67,
                duration_seconds=LIMIT_OPEN_PARTIAL_SECONDS,
                reprice_interval=LIMIT_OPEN_PARTIAL_SECONDS,
                min_price_pct_of_fair=1.0 - MIN_BID_DISCOUNT_PCT / 100.0,
            ),
            # Phase 2.3: sell at bid (aggressive) — tracks bid every 15s
            # min_price_pct_of_fair: refuse to place if bid < fair × floor
            ExecutionPhase(
                pricing="fair", fair_aggression=1.0,
                duration_seconds=LIMIT_OPEN_BID_SECONDS,
                reprice_interval=15,
                min_price_pct_of_fair=1.0 - MIN_BID_DISCOUNT_PCT / 100.0,
            ),
        ]),

        # ── Operational limits ───────────────────────────────────────
        max_concurrent_trades=MAX_CONCURRENT,
        max_trades_per_day=1,
        cooldown_seconds=0,
        check_interval_seconds=CHECK_INTERVAL,

        # ── Callbacks ────────────────────────────────────────────────
        on_trade_opened=_on_trade_opened,
        on_trade_closed=_on_trade_closed,

        # ── Metadata ─────────────────────────────────────────────────
        # RFQ phased open configuration (read by execution_router):
        #   - rfq_min_book_improvement_pct is a callable that computes
        #     the gate dynamically from fair price at trade time
        #   - relax_after=999 ensures the gate never relaxes (no Phase 3)
        metadata={
            "rfq_phased": True,
            "rfq_initial_wait_seconds": RFQ_INITIAL_WAIT,
            "rfq_min_book_improvement_pct": _compute_rfq_gate,
            "rfq_timeout_seconds": RFQ_OPEN_TIMEOUT,
            "rfq_relax_after_seconds": 999,
        },
    )
