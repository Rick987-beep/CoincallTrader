"""
Long Strangle — 2-Hour PnL Monitoring Test

Opens a long strangle immediately and monitors PnL-based exits for 2 hours.
Must be run through main.py to get full hardening (persistence, health
checks, error isolation, retry with exponential backoff).

Purpose:    Validate that open-position PnL monitoring and exit triggers work.

Structure (1DTE):
  - BUY call  δ ≈ +0.08  (0.01 BTC)
  - BUY put   δ ≈ −0.08  (0.01 BTC)

Entry:      Immediately on launch (wide 24h window)
Exit:       PnL ≥ 20% of entry cost  OR  2 hours after fill
Cycles:     1, then auto-stop
Retries:    Up to 3 entry attempts
Execution:  Limit orders (orderbook)

Pass criteria:
  - Either profit_target(20%) fires  → PnL exit works ✓
  - Or max_hold_hours(2) fires       → time fallback works,
    and logs confirm PnL was tracked throughout ✓

Usage:
    # In main.py STRATEGIES list:
    from strategies import long_strangle_pnl_test
    STRATEGIES = [long_strangle_pnl_test]
"""

import logging
from datetime import datetime, timezone, timedelta

from option_selection import strangle
from strategy import StrategyConfig, max_hold_hours, profit_target
from trade_lifecycle import TradeState

logger = logging.getLogger(__name__)


# ── Parameters ───────────────────────────────────────────────────────────────

QTY = 0.01                         # BTC per leg (tiny — orderbook sized)
DTE = 1                            # 1 day to expiry
CALL_DELTA = 0.08                  # target call delta
PUT_DELTA = -0.08                  # target put delta

ENTRY_WINDOW_MIN = 1440            # 24h window — enter immediately on launch

MAX_HOLD_HOURS = 2                 # close after 2 hours if TP not hit

PROFIT_TARGET_PCT = 0.20           # exit when PnL ≥ 20% of entry cost
CHECK_INTERVAL = 10                # seconds between tick evaluations
MAX_CYCLES = 1                     # single test cycle, then auto-stop
MAX_ATTEMPTS_PER_DAY = 3           # entry retries


# ── Multi-Day State ─────────────────────────────────────────────────────────

class _MultiDayState:
    """
    Tracks completed cycles and daily entry attempts across days.

    Separated from the StrategyConfig so state persists across ticks
    and across days within a single process lifetime.
    """

    def __init__(self):
        self.completed_cycles = 0
        self.daily_attempts = 0
        self._last_date = None
        self._runner = None       # set via on_runner_created metadata hook

    # -- Runner attachment ------------------------------------------------

    def set_runner(self, runner):
        """Store runner reference for auto-disable after final cycle."""
        self._runner = runner
        logger.info("Runner attached to multi-day state")

    # -- Entry gate -------------------------------------------------------

    def _reset_if_new_day(self):
        """Reset daily attempt counter on UTC day rollover."""
        today = datetime.now(timezone.utc).date()
        if self._last_date != today:
            if self._last_date is not None:
                logger.info(f"New UTC day ({today}) — resetting daily attempts")
            self.daily_attempts = 0
            self._last_date = today

    def entry_gate(self, account) -> bool:
        """
        Custom entry condition: enforce cycle limit + daily attempt limit.

        Must be placed LAST in entry_conditions so earlier conditions
        (like the time window) filter first and we don't waste attempts
        on out-of-window ticks.

        Signature: (AccountSnapshot) -> bool
        """
        if self.completed_cycles >= MAX_CYCLES:
            logger.debug("All cycles complete — blocking entry")
            return False

        self._reset_if_new_day()

        if self.daily_attempts >= MAX_ATTEMPTS_PER_DAY:
            logger.info(
                f"Daily attempt limit reached "
                f"({self.daily_attempts}/{MAX_ATTEMPTS_PER_DAY})"
            )
            return False

        self.daily_attempts += 1
        logger.info(
            f"Entry attempt {self.daily_attempts}/{MAX_ATTEMPTS_PER_DAY} today "
            f"(cycle {self.completed_cycles + 1}/{MAX_CYCLES})"
        )
        return True

    # -- Trade-closed callback --------------------------------------------

    def on_trade_closed(self, trade, account):
        """
        Called by StrategyRunner when a trade transitions to CLOSED or FAILED.

        Only CLOSED trades count as completed cycles.  FAILED trades
        (e.g. limit order failed) do not consume a cycle — the
        strategy retries via the daily attempt counter.

        Signature: (TradeLifecycle, AccountSnapshot) -> None
        """
        if trade.state != TradeState.CLOSED:
            logger.warning(
                f"Trade {trade.id} ended as {trade.state.value} "
                f"— not counting as completed cycle"
            )
            return

        self.completed_cycles += 1
        pnl = trade.structure_pnl(account)
        entry_cost = trade.total_entry_cost()
        roi = (pnl / abs(entry_cost) * 100) if entry_cost else 0.0
        hold_s = trade.hold_seconds or 0

        logger.info(
            f"\n═══ CYCLE {self.completed_cycles}/{MAX_CYCLES} COMPLETE ═══\n"
            f"  Entry Cost:  ${entry_cost:.2f}\n"
            f"  PnL:         ${pnl:.2f}\n"
            f"  ROI:         {roi:+.1f}%\n"
            f"  Hold Time:   {hold_s / 60:.1f} min\n"
            f"  Trade ID:    {trade.id}"
        )

        if self.completed_cycles >= MAX_CYCLES:
            logger.info(
                f"\n{'=' * 60}\n"
                f"✓ PnL MONITORING TEST COMPLETE\n"
                f"{'=' * 60}"
            )
            if self._runner:
                self._runner.disable()   # triggers main.py auto-shutdown


# Module-level state: persists across the runner's lifetime
_state = _MultiDayState()


# ── Entry condition: recurring daily time window ────────────────────────────

def _daily_minute_window(hour: int, minute: int, duration_min: int):
    """
    Allow entry during a specific time-of-day window, every day.

    Unlike utc_time_window() which is date-bound (computed once at init),
    this recalculates from the current UTC date on every tick, so it
    works correctly across midnight boundaries and multi-day runs.

    Signature: (AccountSnapshot) -> bool
    """
    def _check(account) -> bool:
        now = datetime.now(timezone.utc)
        start = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        end = start + timedelta(minutes=duration_min)
        ok = start <= now < end
        if not ok:
            logger.debug(
                f"daily_window: {now.strftime('%H:%M:%S')} not in "
                f"{hour:02d}:{minute:02d}–{end.strftime('%H:%M')}"
            )
        return ok

    _check.__name__ = f"daily_window({hour:02d}:{minute:02d}+{duration_min}m)"
    return _check


# ── Strategy Factory ────────────────────────────────────────────────────────

def long_strangle_pnl_test() -> StrategyConfig:
    """
    Long strangle 2-hour PnL monitoring test.

    Returns a StrategyConfig for registration in main.py's STRATEGIES list.
    After StrategyRunner creation, main.py calls the 'on_runner_created'
    metadata hook to attach the runner for auto-stop.
    """
    now_utc = datetime.now(timezone.utc)
    logger.info(
        f"\n{'=' * 60}\n"
        f"LONG STRANGLE — PnL MONITORING TEST\n"
        f"  Launch:  {now_utc.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
        f"  Exit:    TP {PROFIT_TARGET_PCT * 100:.0f}% or "
        f"max hold {MAX_HOLD_HOURS}h\n"
        f"  Legs:    buy C(δ{CALL_DELTA}) + P(δ{PUT_DELTA}), "
        f"{QTY} BTC, {DTE}DTE\n"
        f"  Cycles:  {MAX_CYCLES}\n"
        f"  Retries: {MAX_ATTEMPTS_PER_DAY}\n"
        f"  Mode:    limit orders (orderbook)\n"
        f"{'=' * 60}"
    )

    return StrategyConfig(
        name="long_strangle_pnl_test",
        legs=strangle(
            qty=QTY,
            call_delta=CALL_DELTA,
            put_delta=PUT_DELTA,
            dte=DTE,
            side=1,   # BUY
        ),
        entry_conditions=[
            _daily_minute_window(0, 0, ENTRY_WINDOW_MIN),  # wide open — enter now
            _state.entry_gate,   # LAST — increments attempt counter
        ],
        exit_conditions=[
            profit_target(PROFIT_TARGET_PCT * 100),  # 20% of entry cost
            max_hold_hours(MAX_HOLD_HOURS),           # 2h fallback
        ],
        execution_mode="limit",
        max_concurrent_trades=1,
        max_trades_per_day=0,          # managed by entry_gate (not framework)
        cooldown_seconds=60,
        check_interval_seconds=CHECK_INTERVAL,
        on_trade_closed=_state.on_trade_closed,
        metadata={
            "on_runner_created": _state.set_runner,
        },
    )
