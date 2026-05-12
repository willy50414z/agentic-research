---
name: ml-trading-strategy
description: Develop and validate ML-based trading strategies while checking for lookahead bias, overfitting, weak baselines, and unrealistic backtest assumptions. Use when the task involves ML model design, ML feature engineering, ML validation, or ML-driven trading strategy review.
---

# ML Trading Strategy Development Skill

This skill provides mandatory guidelines for developing and validating ML-based trading strategies (specifically for Binance Futures and Freqtrade). These rules are designed to prevent common pitfalls like lookahead bias, overfitting, and unrealistic backtesting results.

## 1. ML Training Precautions

- **Lookahead Bias Prevention:**
    - Never use future data in feature engineering.
    - Be extremely cautious with operations like `shift(-N)`, `rolling` (if window isn't left-aligned), or any data transformation that requires knowledge of the future.
    - Informative timeframe data (e.g., 1h, 4h, 1d) MUST be merged with the base timeframe (15m) using a method that ensures the informative candle is fully closed before its data is available to the base candle (use `merge_informative_pair` correctly).

- **Data Splitting & Validation:**
    - **Walk-forward Validation:** Prioritize walk-forward out-of-sample (OOS) testing over random shuffling.
    - **In-sample (IS) vs. Out-of-sample (OOS):** Monitor performance on OOS data. Significant performance degradation in OOS indicates overfitting.
    - **Sequence & Horizon:** Clearly define `sequence_length` (e.g., 18 steps for 15m) and `prediction_horizon` (e.g., 5 steps ahead).

- **Baseline Comparison:**
    - Every ML model (e.g., LSTM) must be compared against simple baselines:
        1. **Buy & Hold (B&H)**
        2. **Rule-based baseline** (e.g., simple MA cross)
        3. **Non-deep-learning baseline** (e.g., XGBoost, Linear Regression)
    - If the ML model doesn't outperform the baseline in OOS, it's not ready for deployment.

- **Labeling (Target Construction):**
    - Targets must be logically sound (e.g., N-step ahead price change, or K-period max return).
    - Avoid ambiguous labels that might inadvertently leak future volatility or price movement.

## 2. Backtest & Result Validation Precautions

- **Real-world Constraints:**
    - **Fees & Slippage:** Always include realistic transaction fees (e.g., 0.04% for Binance Futures) and slippage.
    - **Position Sizing:** Use realistic `stake_amount`, `max_open_trades`, and `starting_balance`.
    - **Order Types:** Use limit orders where possible, but account for market order slippage if the strategy requires it.

- **Performance Metrics (Beyond Total Profit):**
    - **Sharpe Ratio & Sortino Ratio:** Evaluate risk-adjusted returns.
    - **Profit Factor:** Gross Profit / Gross Loss (aim for > 1.5).
    - **Calmar Ratio:** Annualized Return / Max Drawdown.
    - **Max Drawdown (MDD):** Evaluate the worst-case scenario.
    - **Win Rate & Profit per Trade:** Analyze the average trade quality.

- **Informative Pair Handling:**
    - Ensure informative timeframes are resampled and merged correctly to avoid lookahead bias.
    - Validate that the strategy doesn't "peek" into the future during inference.

- **Consistency Check:**
    - The backtest period used for final validation must be the same as the "Out-of-sample" or "Test" period used during training to ensure the model's predictive power is truly tested.

## 3. Workflow Implementation

- **Thought First:** Before writing any code, state the logic behind the feature/model change and how it addresses lookahead bias or overfitting.
- **Verification:** After every training or backtest run, perform a "Bias Check":
    1. Does the model use any `shift(-N)` or lookahead features?
    2. Is the OOS performance consistent with IS?
    3. Is the profit factor realistic (>3.0 is usually a red flag for bias)?
