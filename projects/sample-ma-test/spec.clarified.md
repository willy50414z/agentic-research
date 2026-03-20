# Spec Clarifications

**Project**: ma-crossover-alpha  
**Generated**: 2026-03-20 04:32 UTC

> Fill in each **Answer** below, then run `./resume.sh`.

---

## 1. `research.signals.entry`

**Original value**  
```
SMA(close, fast_period) crosses above SMA(close, slow_period)
```

**Question**  
入場信號確認方式？ (1) 單根 K 棒收盤 (2) 連續 N 根收盤

**Answer**  
<!-- fill in below -->
單根 K 棒收盤確認（即當日收盤時 SMA 已完成交叉）


---

## 2. `research.performance.sharpe_ratio.min`

**Original value**  
```
1.2
```

**Question**  
Sharpe ratio 為年化 (252 交易日) 還是 per-period？

**Answer**  
<!-- fill in below -->
年化（252 交易日）


---

<details>
<summary>Original spec snapshot</summary>

```yaml
project:
  label: ma-crossover-alpha
  name: MA Crossover Alpha
research:
  hypothesis: 'Moving average crossover strategy can capture trend momentum in US
    equities at the daily timeframe. Fast MA crossing above slow MA generates positive
    expected value with acceptable drawdown and consistent win rate across out-of-sample
    periods.


    ---'
  universe:
    instruments:
    - AAPL
    - MSFT
    - GOOG
    - AMZN
    - NVDA
    asset_class: equity
    frequency: daily
  data:
    source: yfinance
    train:
    - '2018-01-01'
    - '2022-12-31'
    test:
    - '2023-01-01'
    - '2024-12-31'
  signals:
    entry: SMA(close, fast_period) crosses above SMA(close, slow_period)
    exit: SMA(close, fast_period) crosses below SMA(close, slow_period)
  performance:
    sharpe_ratio:
      min: 1.0
    max_drawdown:
      max: 0.2
    profit_factor:
      min: 1.2
    win_rate:
      min: 0.45
    oos_profit_factor:
      min: 1.1
  plugin: quant_strategy
  review_interval: 5
  max_loops: 20
  notes: Prioritize shorter fast periods to reduce overfit. Require OOS profit factor
    ≥ 1.1 before accepting any parameter set.
```
</details>
