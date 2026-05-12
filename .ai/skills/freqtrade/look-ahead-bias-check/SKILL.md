---
name: look-ahead-bias-check
description: Inspect a Freqtrade strategy file for look-ahead bias patterns before running a backtest. This is a mandatory preflight gate — run it on every new strategy implementation and every logic change, not just when win rate looks suspiciously high.
---

# Look-Ahead Bias Check Skill

Look-ahead bias is the most common source of unrealistically inflated backtest results. It is undetectable from metrics alone — a biased strategy can show any profit factor. Structural code inspection is the only reliable detection method.

## Quick Reference

This check is MANDATORY before every backtest. It takes under 5 minutes.

| Check | Command / Method |
|-------|----------------|
| Negative shift (reads future bars) | `grep -n "shift(-" freqtrade/strategies/<StrategyName>.py` |
| Informative pair without shift | `grep -n "merge_informative_pair" freqtrade/strategies/<StrategyName>.py` |
| Close price in entry condition | Manual inspection of `populate_entry_trend` |
| Future candle indicators | Manual inspection of indicator calculation |

Verdict: **CLEAN** (proceed) | **SUSPECT** (inspect flagged patterns) | **CONFIRMED BIAS** (fix before backtesting)

---

## Workflow

### Step 1 — Scan for negative shift

```bash
grep -n "shift(-" freqtrade/strategies/<StrategyName>.py
```

Any `shift(-N)` with N > 0 reads future bar data. This is almost always look-ahead bias unless the logic explicitly documents why forward-looking data is intentional and bounded to non-predictive metadata (rare).

Flag as: **CONFIRMED BIAS** if found in entry/exit condition columns.

### Step 2 — Scan for informative pair usage

```bash
grep -n "merge_informative_pair\|informative_pairs" freqtrade/strategies/<StrategyName>.py
```

For each `merge_informative_pair` call, verify:
- `shift=True` is used (the default, adds 1-bar lag to prevent lookahead)
- `ffill=True` is not used with `shift=False` in combination
- The informative pair timeframe is not the same as the strategy timeframe (same-timeframe informative pairs with shift=False are lookahead)

Flag as: **SUSPECT** if any `merge_informative_pair` uses `shift=False`.

### Step 3 — Inspect populate_entry_trend and populate_exit_trend

Read the entry and exit signal generation logic. Verify:

- [ ] Entry conditions use only values that are available at bar open (not the current bar's close/high/low computed mid-bar)
- [ ] No indicator is computed from a column that contains the close price of the current unfinished bar
- [ ] `last_candle_seen_at` or equivalent lookahead-free candle references are used if needed

Flag as: **SUSPECT** if any condition references the current bar's close price directly as an entry trigger.

### Step 4 — Inspect indicator computation for future leakage

Review `populate_indicators()`. Verify:
- No rolling window uses a `min_periods` of 0 that would include the current bar's forward-facing incomplete data
- No ML model was trained on data that includes the test period (training data contamination)
- No external feature (e.g., sentiment score) uses data timestamped after bar close

### Step 5 — Record verdict

Write one of:

```
Look-Ahead Bias Check: CLEAN
No patterns found. Strategy may proceed to backtest.
```

```
Look-Ahead Bias Check: SUSPECT
Patterns found:
- Line 142: merge_informative_pair with shift=False — verify this is intentional
Action: Inspect line 142 before proceeding. If confirmed safe, document why.
```

```
Look-Ahead Bias Check: CONFIRMED BIAS
Patterns found:
- Line 87: dataframe['future_signal'] = dataframe['close'].shift(-1)
Action: Fix before any backtest. Results are invalid until resolved.
```

---

## Common Freqtrade-Specific Lookahead Patterns

| Pattern | Biased? | Why |
|---------|---------|-----|
| `shift(-1)` in entry/exit column | Yes | Reads next bar's data |
| `merge_informative_pair(..., shift=False)` | Usually yes | No lag added to higher TF data |
| `rolling(N).mean()` on close | No | Uses only past data |
| `ta.EMA(dataframe, timeperiod=N)` | No | TA-Lib uses only past data |
| `dataframe['close'] > dataframe['open']` as entry | No | Current bar data is acceptable for entry AT bar close |
| ML model trained on full dataset including test period | Yes | Training contamination |

## Code Reference

| Purpose | Path |
|---------|------|
| Strategy files | `freqtrade/strategies/` |
| Freqtrade informative pairs docs | https://www.freqtrade.io/en/stable/strategy-advanced/#informative-pairs |
