---
name: regime-filter-validation
description: Validate that a strategy's regime filter is correctly calibrated — neither blocking too many trades (under-exposure) nor allowing too many (ineffective filter). Use after a backtest when the strategy uses any regime/market condition filter (ADX, volatility, trend strength, etc.).
---

# Regime Filter Validation Skill

A regime filter that sounds good in theory can fail silently in two ways:
- **Over-filtering**: blocks 80%+ of candles, leaves too few trades for statistical significance
- **Under-filtering**: passes 90%+ of candles, provides no real protection against bad market conditions

## Quick Reference

| Check | Target range | Action if outside |
|-------|-------------|-------------------|
| Pass rate (% candles passing filter) | 20%–80% | Recalibrate filter threshold |
| Filtered win rate vs unfiltered win rate | Filtered > Unfiltered by ≥ 5% | Filter is ineffective; consider removing |
| Trade count in filtered subset | ≥ 30 trades | Extend backtest period or loosen filter |
| OOS pass rate vs IS pass rate | Within 10% | Regime filter may be overfit to IS period |

---

## Workflow

### Step 1 — Identify the regime filter

Read the strategy spec (README.md or strategy .py file) to identify the regime condition, e.g.:
- `ADX > 25` (trend strength)
- `ATR_ratio > 1.2` (volatility expansion)
- `EMA50 > EMA200` (trend direction)

### Step 2 — Compute pass rate from backtest signals

From the backtest signals file (`<stem>_signals.json` or `.pkl`) or analysis output:
- Count total candles in the backtest period
- Count candles where the regime condition is True
- Compute: `pass_rate = filtered_candles / total_candles`

If the signals file is not available, estimate from `dbg_` columns in the analysis output.

### Step 3 — Compare win rate inside vs outside filter

From trade records:
- `filtered_wr` = win rate of trades taken when regime filter = True
- `unfiltered_wr` = win rate of ALL trades (no regime filter applied)

If `filtered_wr - unfiltered_wr < 5%`, the filter adds minimal value.

### Step 4 — Check statistical significance

Minimum 30 trades in the filtered subset for any metric comparison to be meaningful. If fewer than 30 trades:
- Either extend the backtest timerange
- Or loosen the regime filter threshold
- Do NOT tune the threshold to maximize win rate on < 30 trades (overfitting)

### Step 5 — Verdict and action

| Scenario | Verdict | Action |
|----------|---------|--------|
| Pass rate 20–80%, filtered_wr > unfiltered_wr by ≥ 5%, ≥ 30 trades | CALIBRATED | Filter is working. Proceed. |
| Pass rate < 20% | OVER-FILTERED | Loosen threshold. Too few trades for reliable statistics. |
| Pass rate > 80% | UNDER-FILTERED | Tighten threshold or reconsider filter concept. |
| filtered_wr ≤ unfiltered_wr | INEFFECTIVE | Filter does not improve signal quality. Remove or redesign. |
| < 30 trades in subset | INSUFFICIENT DATA | Extend period before concluding. |

### Step 6 — Record findings

Add to the analysis summary (`sessions/<date>-analysis-summary.md`):

```markdown
## Regime Filter Validation

- Filter condition: <e.g., ADX > 25>
- Pass rate (IS): <XX%>
- Filtered win rate: <XX%>
- Unfiltered win rate: <XX%>
- Trade count in filtered subset: <N>
- Verdict: CALIBRATED | OVER-FILTERED | UNDER-FILTERED | INEFFECTIVE | INSUFFICIENT DATA
- Action: <next step if not CALIBRATED>
```

## When to Trigger

Trigger this skill after every backtest when:
- The strategy has one or more regime/market condition filters
- The win rate is suspiciously high (possible over-filtering)
- Trade count is lower than expected

Do NOT trigger for:
- Strategies with no regime filters
- Hyperopt runs (wait for final parameter selection)
