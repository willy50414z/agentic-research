# Research Spec: MA Crossover Alpha

_Edit this file freely, then run `./start.sh`. The AI agent will parse it._

---

## Hypothesis

Moving average crossover strategy can capture trend momentum in US equities at the daily timeframe. Fast MA crossing above slow MA generates positive expected value with acceptable drawdown and consistent win rate across out-of-sample periods.

---

## Universe

- **Instruments**: AAPL, MSFT, GOOG, AMZN, NVDA
- **Asset class**: equity
- **Frequency**: daily

---

## Data

- **Source**: yfinance
- **Training period**: 2018-01-01 to 2022-12-31
- **Test period (OOS)**: 2023-01-01 to 2024-12-31

---

## Signals

- **Entry**: SMA(close, fast_period) crosses above SMA(close, slow_period)
- **Exit**: SMA(close, fast_period) crosses below SMA(close, slow_period)

---

## Optimization

- **Method**: Grid search
- **Parameters**:
  - `fast_period`: 5, 10, 15, 20
  - `slow_period`: 40, 60, 80, 100

---

## Performance Thresholds

| Metric             | Target |
|--------------------|--------|
| Sharpe Ratio       | ≥ 1.2  |
| Max Drawdown       | ≤ 15%  |
| Profit Factor      | ≥ 1.3  |
| Win Rate           | ≥ 48%  |
| OOS Profit Factor  | ≥ 1.1  |

---

## Tools

- **Backtest engine**: freqtrade
- **Data source**: yfinance

---

## Settings

- **Plugin**: quant_strategy
- **Review every**: 5 loops
- **Max loops**: 20

---

## Notes

Prioritize shorter fast periods to reduce overfit. Require OOS profit factor ≥ 1.1 before accepting any parameter set.
