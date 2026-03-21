# Research Spec: {{PROJECT_NAME}}

_Edit this file freely, then run `./start.sh`. The AI agent will parse it._

---

## Hypothesis

快慢均線交叉可以捕捉趨勢動能，在日線級別的美股上產生正期望值。

---

## Universe

- **Instruments**: AAPL, MSFT, GOOG
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
  - `fast_period`: 5, 10, 15, 20, 30
  - `slow_period`: 40, 60, 80, 100

---

## Performance Thresholds

| Metric             | Target |
|--------------------|--------|
| Sharpe Ratio       | ≥ 1.0  |
| Max Drawdown       | ≤ 20%  |
| Profit Factor      | ≥ 1.2  |
| Win Rate           | ≥ 45%  |
| OOS Profit Factor  | ≥ 1.1  |

---

## Tools

- **Backtest engine**: freqtrade
- **Data source**: yfinance

---

## Settings

- **Plugin**: quant_alpha
- **Review every**: 5 loops
- **Max loops**: 30

---

## Notes

優先測試短週期交叉，避免參數過擬合。
