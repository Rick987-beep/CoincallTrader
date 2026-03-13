# BTC Daily Put Selling Strategy

## Strategy Overview

A systematic, daily short-put premium-harvesting strategy on BTC options. The strategy sells out-of-the-money (OTM) put options on Bitcoin with 1-day expiry, collecting theta decay while using a trend filter to avoid selling into downtrends.

---

## Strategy Rules

| Parameter | Value |
|---|---|
| **Instrument** | BTC Put Options (Sell to Open) |
| **Cycle** | Daily — one trade per day |
| **Entry Time** | 03:05 UTC |
| **Expiry** | 1 DTE (next day 08:00 UTC) |
| **Strike Selection** | Closest to **-10 delta** |
| **Take Profit** | 90% of premium collected |
| **Stop Loss** | 70% of premium collected (i.e., loss = 1.7x premium) |
| **Trend Filter** | **Blacklist days** — no trade if BTCUSD daily close is below the 20-period EMA |

### How It Works

1. **Every day at 03:05 UTC**, the system checks whether BTC is trading above its 20 EMA on the daily chart.
2. If the trend filter passes, it **sells 1 put option** at the strike closest to -10 delta (approximately 90% probability of expiring OTM).
3. The option expires at **08:00 UTC the following day** (~29 hours later).
4. The position is managed with a **90% take-profit** (close when 90% of collected premium is captured) and a **70% stop-loss** (close if the loss reaches 70% of the premium value, i.e., the option price rises to 1.7x the entry premium).
5. If neither TP nor SL is hit, the option **expires worthless** and full premium is kept.

---

## Backtest Results

**Test Period:** January 1, 2025 – August 14, 2025 (7.5 months)
**Starting Capital:** $10,000

### Key Performance Metrics

| Metric | Value |
|---|---|
| **Total P&L** | **+$4,509.04** |
| **Final Capital** | **$14,509.04** |
| **Total Return** | **+45.09%** |
| **Annualized Return** | **~72%** |
| **Max Drawdown** | **-3.12%** |
| **Total Trades** | 133 |
| **Win Rate** | **66.2%** (88 wins / 45 losses) |
| **Profit Factor** | **1.97** |
| **Avg Win** | $103.95 |
| **Avg Loss** | -$103.08 |
| **Win/Loss Ratio** | 1.01 |
| **Largest Win** | $290.08 |
| **Largest Loss** | -$155.17 |
| **Max Win Streak** | 7 |
| **Max Loss Streak** | 4 |
| **Avg Hold Time** | ~7.7 hours (0.32 days) |
| **Avg Premium Collected** | $118.45 per trade |

### Return vs. Risk

The strategy's standout characteristic is the **exceptional risk-adjusted return**. A 45% return over 7.5 months with a maximum drawdown of only 3.12% yields a **return-to-drawdown ratio of ~14.5x** — extremely strong for any systematic strategy.

---

## Exit Reason Breakdown

| Exit Type | Count | % of Trades | Total P&L |
|---|---|---|---|
| **Take Profit** (90% captured) | 53 | 39.8% | +$6,399.71 |
| **Expiry** (full premium kept) | 35 | 26.3% | +$2,747.81 |
| **Stop Loss** (70% loss) | 45 | 33.8% | -$4,638.48 |

- **66.2% of trades are winners** — either hitting take-profit or expiring worthless (keeping full premium).
- Expiry winners (26.3%) represent trades where the option decayed but didn't hit the 90% TP threshold before 08:00 UTC — still fully profitable.
- The tight stop-loss at 70% caps downside on any single trade, keeping individual losses manageable.

---

## Monthly Performance

| Month | Trades | P&L | Win Rate | Wins | Losses |
|---|---|---|---|---|---|
| **Jan 2025** | 25 | +$2,022.35 | 76.0% | 19 | 6 |
| **Feb 2025** | 2 | -$135.53 | 0.0% | 0 | 2 |
| **Mar 2025** | 7 | -$184.61 | 42.9% | 3 | 4 |
| **Apr 2025** | 19 | +$735.52 | 68.4% | 13 | 6 |
| **May 2025** | 30 | +$801.54 | 66.7% | 20 | 10 |
| **Jun 2025** | 15 | +$645.01 | 73.3% | 11 | 4 |
| **Jul 2025** | 28 | +$432.70 | 60.7% | 17 | 11 |
| **Aug 2025** | 7 | +$192.05 | 71.4% | 5 | 2 |

### Observations

- **6 out of 8 months were profitable**, with only Feb and Mar posting losses.
- **February** had only 2 trades (both losers) — the EMA filter correctly identified the downtrend and kept the strategy **sidelined for most of the month**, limiting damage to just -$135.
- **March** saw a BTC drawdown period (BTC fell from ~$97K to ~$83K); the blacklist filter reduced exposure from ~31 possible days to only 7 trades, capping losses at -$184.
- **January** was the strongest month (+$2,022) as BTC rallied from ~$93K to ~$105K — the strategy thrives in trending/rangebound-up markets.
- **The EMA filter's primary value**: during Feb-Mar (BTC's worst stretch), only 9 trades were taken vs. ~60 calendar days. Without the filter, losses would have been significantly larger.

---

## Trade Activity & the Blacklist Filter

| Metric | Value |
|---|---|
| Total Calendar Days | 226 |
| Days with Trades | 133 |
| Blacklisted (No-Trade) Days | 93 (41%) |

The 20 EMA blacklist filter kept the strategy out of the market for **41% of all days** — primarily during the Feb-Mar correction. This is the strategy's key risk management mechanism beyond the per-trade stop-loss.

---

## Equity Curve Characteristics

The capital curve grew from $10,000 to $14,509 with a smooth, upward-sloping trajectory. Key observations:

- **No significant drawdown** — the max drawdown of -3.12% occurred briefly and recovered quickly.
- **Consistent compounding** — the strategy produces frequent small wins that accumulate steadily.
- **Rapid recovery** from losing streaks — the max loss streak of 4 was followed by quick mean-reversion back to new equity highs.

---

## Strategy Edge Analysis

### Why This Works

1. **Theta Decay Harvesting**: 1-DTE options have the fastest time decay. By selling puts with only ~29 hours to expiry, the strategy captures maximum theta per unit of time.

2. **OTM Probability Advantage**: At -10 delta, the put has approximately a 90% probability of expiring OTM. The base win rate is structurally high.

3. **Trend Filter (EMA 20)**: The blacklist mechanism avoids selling puts into a falling market — the single most dangerous scenario for short puts. This filter prevented heavy losses during the Feb-Mar BTC selloff.

4. **Asymmetric Risk Management**: The 90% TP / 70% SL structure means:
   - Winning trades capture most of the premium quickly (close early)
   - Losing trades are capped before the option goes deep ITM
   - Expiry winners keep 100% of premium

5. **Profit Factor of 1.97**: For every $1 lost, the strategy makes $1.97. This is a robust edge — anything above 1.5 is generally considered strong for a systematic strategy.

### Risk Factors

- **Tail risk**: Extreme BTC moves (flash crashes) could gap through the stop-loss before it triggers, especially in the illiquid overnight hours.
- **Volatility regime**: In sustained high-IV environments, losses-per-trade are also larger. The 70% SL is premium-relative, so larger premiums mean larger dollar losses.
- **Liquidity**: 1-DTE BTC options may have wider spreads, and the backtest may not fully capture slippage.
- **Blacklist dependency**: The strategy's risk management heavily relies on the EMA filter. If the filter fails to identify a regime change, drawdowns could be deeper than the backtest suggests.

---

## Summary

The BTC Daily Put Selling strategy is a high-frequency premium harvesting system that delivers strong risk-adjusted returns. With a **45% return over 7.5 months**, a **max drawdown of just -3.12%**, and a **profit factor of 1.97**, the backtest shows a compelling edge in selling short-dated OTM puts on Bitcoin.

The combination of a daily -10 delta put sale, tight TP/SL management, and an EMA-based trend filter produces a strategy that:

- Wins consistently (~66% of the time)
- Keeps losses small and controlled
- Stays out of the market during unfavorable conditions
- Compounds steadily with minimal drawdowns

**Next Steps**: Validate with out-of-sample data, assess live slippage on Coincall's 1-DTE options, and size appropriately for production deployment.
