---
name: strategy-performance-evaluation
description: Launch-gate evaluation framework for trading strategies. Use when an agent must decide whether a strategy is deployable, borderline, or reject based on profile-specific thresholds, statistical significance, OOS degradation, regime coverage, and crypto-specific risk adjustments.
---

# Strategy Performance Evaluation

This skill is the authoritative launch gate for deciding whether a trading strategy can go live.

Use this skill when:
- scoring a backtest or OOS result after an iteration
- deciding KEEP / NEED MORE DATA / REVERT
- deciding whether a strategy is allowed to enter deployment preparation
- writing the performance section of `analysis-summary.md`

The evaluation must always be profile-based. Do not apply a single universal threshold to all strategies.

---

## Section 1 - Launch Gate Workflow

Evaluate in this order. Stop only when the rule says to stop.

1. Identify the strategy profile:
   - Profile A: High Frequency / Statistical Arbitrage
   - Profile B: Trend Following
   - Profile C: Mean Reversion
2. Check statistical significance gate.
3. Check automatic elimination red lines.
4. Apply IS/OOS degradation grading.
5. Score the 8 core metrics against the selected profile.
6. Check four-regime market coverage.
7. Apply crypto-specific adjustments.
8. Emit both:
   - Tier verdict: `REJECT | BORDERLINE | PROMISING`
   - Launch verdict: `BLOCK | CONDITIONAL | GO-LIVE-READY`

Decision mapping:
- `REJECT` -> `REVERT`
- `BORDERLINE` -> `NEED MORE DATA`
- `PROMISING` but launch gate not fully passed -> `KEEP`, but not deployable yet
- `PROMISING` and launch gate fully passed -> `KEEP` and eligible for deployment prep

---

## Section 2 - Strategy Profiles

Choose exactly one primary profile before evaluating.

### 2.1 Profile A - High Frequency / Statistical Arbitrage

Objective: maximize stability, minimize risk, and survive costs.

Required launch characteristics:
- very smooth upward equity curve
- highly stable edge after fees and slippage
- strict single-trade loss control

Hard profile requirements:
- Sharpe > 2.0
- Max Drawdown < 5%
- Win Rate > 55%
- Single-trade max loss <= 1% of total capital

### 2.2 Profile B - Trend Following

Objective: tolerate low win rate, rely on large winners, and preserve convex payoff.

Hard profile requirements:
- Sharpe > 1.0
- Profit/Loss Ratio > 2.5
- Win Rate between 35% and 45%
- Max Drawdown < 20%

Interpretation:
- frequent stop-outs are normal
- low win rate alone is not a problem
- P/L ratio is the core survival metric

### 2.3 Profile C - Mean Reversion

Objective: monetize range-bound markets while controlling crash risk.

Hard profile requirements:
- Sharpe > 1.5
- Win Rate > 60%
- Profit/Loss Ratio between 0.8 and 1.2

Mandatory protection:
- must include a hard time-based exit, or
- must include a market-volatility protection mechanism such as VIX / volatility regime guard

If neither protection exists, the strategy is `REJECT` regardless of headline metrics.

---

## Section 3 - Statistical Significance Gate

These are minimum evidence requirements before any launch discussion.

### 3.1 Minimum sample size

| Gate | Requirement |
|------|-------------|
| IS sample | >= 100 trades |
| OOS sample | >= 30 trades |

If either fails:
- Tier verdict cannot exceed `BORDERLINE`
- Launch verdict is `BLOCK`

### 3.2 Profit Factor confidence adjustment

Near-threshold PF values must be adjusted downward by trade-count confidence penalty.

| Trade count | PF confidence penalty |
|-------------|------------------------|
| 30-49 | 0.30 |
| 50-99 | 0.20 |
| 100-199 | 0.10 |
| >= 200 | 0.05 |

Use:

```text
PF_adjusted = PF_reported - confidence_penalty
```

Launch and tier decisions must use `PF_adjusted`, not raw PF, whenever PF is close to a threshold.

Examples:
- OOS PF 1.20 with 35 trades -> adjusted PF 0.90
- OOS PF 1.35 with 120 trades -> adjusted PF 1.25

---

## Section 4 - IS/OOS Degradation Grading

Compute:

```text
Degradation Ratio = OOS PF / IS PF
```

Apply these gates:

| Degradation ratio | Grade | Meaning | Action |
|-------------------|-------|---------|--------|
| >= 0.70 | Healthy | acceptable generalization | no downgrade |
| 0.50 to < 0.70 | Warning | moderate degradation | `BORDERLINE` minimum |
| 0.30 to < 0.50 | Severe | strong overfitting signal | `BORDERLINE` and redesign required |
| < 0.30 | Failure | edge collapsed out of sample | `REJECT` |

Apply the same logic to Sharpe as a secondary check:
- OOS Sharpe / IS Sharpe < 0.40 -> add overfitting warning

---

## Section 5 - Automatic Elimination Red Lines

Any one of these red lines forces `REJECT` unless stated otherwise.

| Code | Rule |
|------|------|
| R1 | IS trades < 100 |
| R2 | OOS trades < 30 and result is being considered for launch |
| R3 | Top 3 trades contribute > 40% of total profit |
| R4 | Any single calendar year contributes > 70% of total profit |
| R5 | Win Rate > 80% without exceptional supporting explanation |
| R6 | Look-ahead bias or same-candle-close style leakage is detected |
| R7 | 0% fees or 0% slippage used in the backtest |
| R8 | Strategy fails after conservative fee, slippage, and funding adjustments |
| R9 | Strategy loses money in 2 or more market regimes |
| R10 | Profile C lacks time-exit or volatility protection |
| R11 | Profile A single-trade max loss > 1% of capital |
| R12 | Reported annualized metrics are not normalized to 365-day crypto basis |

`Win Rate > 80%` is not an automatic proof of leakage, but it is a hard launch red flag in this repository and must be rejected unless manually justified with audited execution logic.

---

## Section 6 - Eight Core Metrics Threshold Matrix

Always score all 8 metrics:
- Profit Factor
- Sharpe
- Sortino
- Calmar
- Max Drawdown
- Win Rate
- Risk:Reward or Profit/Loss Ratio
- Recovery Factor

Use the table for the chosen profile.

### 6.1 Profile A Thresholds

| Metric | Launch floor | Strong |
|--------|--------------|--------|
| Profit Factor | >= 1.80 | >= 2.20 |
| Sharpe | > 2.00 | >= 2.50 |
| Sortino | >= 2.50 | >= 3.00 |
| Calmar | >= 2.00 | >= 3.00 |
| Max Drawdown | < 5% | < 3% |
| Win Rate | > 55% | >= 60% |
| Risk:Reward / P:L | >= 0.8 | >= 1.0 |
| Recovery Factor | >= 3.0 | >= 5.0 |

Additional launch checks for Profile A:
- single-trade max loss <= 1% capital
- profitable after conservative fee + slippage + funding drag
- equity curve must be visibly smooth, not event-driven

### 6.2 Profile B Thresholds

| Metric | Launch floor | Strong |
|--------|--------------|--------|
| Profit Factor | >= 1.30 | >= 1.60 |
| Sharpe | > 1.00 | >= 1.50 |
| Sortino | >= 1.50 | >= 2.00 |
| Calmar | >= 0.80 | >= 1.20 |
| Max Drawdown | < 20% | < 15% |
| Win Rate | 35% to 45% | 38% to 45% |
| Risk:Reward / P:L | > 2.50 | >= 3.00 |
| Recovery Factor | >= 1.5 | >= 2.5 |

Additional launch checks for Profile B:
- low win rate is acceptable only if P/L ratio remains dominant
- do not reject solely for frequent stop losses
- must show at least one real trend-capture regime with outsized winner retention

### 6.3 Profile C Thresholds

| Metric | Launch floor | Strong |
|--------|--------------|--------|
| Profit Factor | >= 1.50 | >= 1.80 |
| Sharpe | > 1.50 | >= 2.00 |
| Sortino | >= 2.00 | >= 2.50 |
| Calmar | >= 1.20 | >= 1.80 |
| Max Drawdown | < 12% | < 8% |
| Win Rate | > 60% | >= 65% |
| Risk:Reward / P:L | 0.8 to 1.2 | 0.9 to 1.1 |
| Recovery Factor | >= 2.0 | >= 3.0 |

Additional launch checks for Profile C:
- mandatory time exit or volatility protection
- crash and one-sided trend defense must be explicit
- strong sideways performance alone is insufficient if crash containment is weak

### 6.4 Generic fallback thresholds

Use this only if the strategy truly does not fit A/B/C. If uncertain, force the author to choose a profile.

| Metric | Minimum viable |
|--------|----------------|
| Profit Factor | >= 1.40 |
| Sharpe | >= 1.00 |
| Sortino | >= 1.20 |
| Calmar | >= 0.80 |
| Max Drawdown | <= 20% |
| Win Rate | >= 40% |
| Risk:Reward / P:L | >= 1.00 |
| Recovery Factor | >= 1.50 |

---

## Section 7 - Four-Regime Market Evaluation

The strategy must be evaluated separately in all four regimes:
- Bull trending
- Bear trending
- Sideways low-vol
- Sideways high-vol

Suggested regime proxy:
- BTC trend and volatility state

### 7.1 Regime definitions

| Regime | Definition |
|--------|------------|
| Bull trending | Price above 20-week SMA and directional trend is positive |
| Bear trending | Price below 20-week SMA and directional trend is negative |
| Sideways low-vol | low ADX, compressed range, subdued ATR |
| Sideways high-vol | weak trend but elevated ATR and wide swings |

### 7.2 Coverage rules

| Rule | Requirement |
|------|-------------|
| Minimum covered regimes | 4 evaluated, 3 profitable minimum |
| Hard failure | losing in 2 or more regimes |
| Borderline | losing in exactly 1 regime without explicit documented exclusion |

Profile interpretation guidance:
- Profile A should not materially fail in any regime
- Profile B may be weaker in sideways low-vol, but failure must be documented and compensated elsewhere
- Profile C must prove it can survive bear trending and sideways high-vol without collapse

---

## Section 8 - Crypto-Specific Launch Adjustments

These adjustments are mandatory for crypto strategies.

### 8.1 Annualization basis

Use 365-day annualization.

If metrics use 252-day annualization:
- do not compare directly to this skill's thresholds
- normalize first

### 8.2 Fee model

Minimum conservative modeled fee:
- 0.05% per side

Backtests using less than this are not launch-grade.

### 8.3 Slippage

Minimum launch retest:
- 0.10% slippage per side

The strategy must remain profitable after slippage retest.

### 8.4 Funding rate drag

For perpetual futures, include funding drag when average holding period makes it relevant.

Minimum rule:
- if average holding time > 8 hours, evaluate funding drag
- if data unavailable, apply conservative post-hoc assumption

Funding drag must downgrade the verdict if adjusted PF falls below threshold.

### 8.5 Weekend liquidity

Check weekend degradation explicitly.

Flag as `BORDERLINE` if:
- weekend Sharpe is more than 30% below weekday Sharpe

### 8.6 Liquidation cascade stress

Run at least one stress review around a major liquidation period.

Examples:
- May 2021
- November 2022

Profile guidance:
- Profile B must be tested for reversal whipsaw after cascade moves
- Profile C must be tested for repeated bottom-fishing failure

---

## Section 9 - Verdict Rules

### 9.1 Tier verdict

| Tier | Meaning |
|------|---------|
| REJECT | invalid, overfit, unsafe, or below minimum standard |
| BORDERLINE | potentially useful, but not launchable yet |
| PROMISING | clears research-quality thresholds and may continue |

### 9.2 Launch verdict

| Launch verdict | Meaning |
|----------------|---------|
| BLOCK | not allowed to proceed toward deployment |
| CONDITIONAL | may continue research/planning, but launch blockers remain |
| GO-LIVE-READY | acceptable for deployment preparation |

### 9.3 Required mapping

Use this mapping:

| Condition | Tier | Launch verdict | Decision |
|-----------|------|----------------|----------|
| Any red line triggered | REJECT | BLOCK | REVERT |
| Statistical gate fails | BORDERLINE minimum | BLOCK | NEED MORE DATA |
| Degradation ratio < 0.30 | REJECT | BLOCK | REVERT |
| 8-core metrics partly fail but no red line | BORDERLINE | CONDITIONAL | NEED MORE DATA |
| Metrics pass but crypto adjustments not validated | PROMISING | CONDITIONAL | KEEP |
| Metrics pass, regimes pass, adjustments pass | PROMISING | GO-LIVE-READY | KEEP |

`GO-LIVE-READY` requires all of the following:
- chosen profile explicitly stated
- statistical gate passed
- no red lines
- degradation ratio >= 0.70 preferred, and never below 0.50
- all 8 core metrics pass the chosen profile
- regime coverage passes
- crypto adjustments validated

---

## Section 10 - Standardized Scorecard Template

Paste this into `analysis-summary.md` when reporting performance.

```markdown
## Strategy Launch Gate Scorecard

### Basic Identification
- Strategy Name:
- Strategy Profile: A | B | C
- Market: Spot | Futures | Perpetual Futures
- Exchange:
- Timeframe:
- IS Period:
- OOS Period:

### Final Verdict
- Tier Verdict: REJECT | BORDERLINE | PROMISING
- Launch Verdict: BLOCK | CONDITIONAL | GO-LIVE-READY
- Decision: REVERT | NEED MORE DATA | KEEP

### Statistical Significance Gate
| Item | Value | Requirement | Status |
|------|-------|-------------|--------|
| IS Trade Count | | >= 100 | PASS / FAIL |
| OOS Trade Count | | >= 30 | PASS / FAIL |
| IS PF Confidence Penalty | | by trade count | INFO |
| OOS PF Confidence Penalty | | by trade count | INFO |
| Adjusted IS PF | | profile threshold | PASS / FAIL |
| Adjusted OOS PF | | profile threshold | PASS / FAIL |

### 8 Core Metrics
| Metric | Value | Profile Floor | Status |
|--------|-------|---------------|--------|
| Profit Factor | | | PASS / FAIL |
| Sharpe | | | PASS / FAIL |
| Sortino | | | PASS / FAIL |
| Calmar | | | PASS / FAIL |
| Max Drawdown | | | PASS / FAIL |
| Win Rate | | | PASS / FAIL |
| Risk:Reward / P:L Ratio | | | PASS / FAIL |
| Recovery Factor | | | PASS / FAIL |

### Profile-Specific Mandatory Checks
| Check | Value | Requirement | Status |
|------|-------|-------------|--------|
| Single-Trade Max Loss | | <= 1% capital for Profile A | PASS / FAIL / N/A |
| Trend Convexity | | P/L > 2.5 for Profile B | PASS / FAIL / N/A |
| Time Exit or Volatility Guard | | mandatory for Profile C | PASS / FAIL / N/A |

### IS/OOS Degradation
| Item | Value | Threshold | Status |
|------|-------|-----------|--------|
| IS PF | | | INFO |
| OOS PF | | | INFO |
| OOS PF / IS PF | | >= 0.70 healthy | PASS / WARN / FAIL |
| IS Sharpe | | | INFO |
| OOS Sharpe | | | INFO |
| OOS Sharpe / IS Sharpe | | >= 0.40 | PASS / WARN / FAIL |

### Regime Evaluation
| Regime | PF | Sharpe | Trades | Status |
|--------|----|--------|--------|--------|
| Bull trending | | | | PASS / FAIL |
| Bear trending | | | | PASS / FAIL |
| Sideways low-vol | | | | PASS / FAIL |
| Sideways high-vol | | | | PASS / FAIL |

### Automatic Elimination Red Lines
| Red Line | Triggered? | Notes |
|----------|------------|------|
| Top 3 trades > 40% of total profit | YES / NO | |
| Single year > 70% of total profit | YES / NO | |
| Win Rate > 80% | YES / NO | |
| Look-ahead bias suspected | YES / NO | |
| 0% fee or 0% slippage backtest | YES / NO | |
| Profile C missing crash protection | YES / NO / N/A | |

### Crypto-Specific Adjustments
| Item | Value | Requirement | Status |
|------|-------|-------------|--------|
| Annualization Basis | | 365 | PASS / FAIL |
| Fee Model | | >= 0.05% per side | PASS / FAIL |
| Slippage Retest PF | | still profitable | PASS / FAIL |
| Funding Drag Applied | YES / NO | required if relevant | PASS / FAIL |
| Weekend vs Weekday Sharpe | | weekend degradation <= 30% preferred | PASS / WARN / FAIL |
| Liquidation Cascade Review | YES / NO | required | PASS / FAIL |

### Summary
- Main strengths:
- Main weaknesses:
- Launch blockers:
- Required next actions:
```

---

## Section 11 - Practical Interpretation Rules

Use these interpretation rules when the metrics appear contradictory.

1. Profile A with high PF but MDD above 5% is not launchable.
2. Profile B with low win rate is acceptable only if P/L ratio remains above 2.5 and OOS degradation is controlled.
3. Profile C with high win rate but missing crash protection is not launchable.
4. High total return never overrides poor risk-adjusted metrics.
5. A strategy that only works in one exceptional year is not considered robust.
6. A strategy whose profits come mainly from a few outlier trades is not considered production-stable.

---

## Section 12 - Integration with Other Skills

Use together with:
- `oos-validation-gate` for quick OOS minimum gate
- `look-ahead-bias-check` before trusting any strong result
- `freqtrade-analysis-workflow` for backtest and analysis flow
- `trade-strategy-improvement-planning` after a promising but non-launchable result
- `trade-strategy-deployment-prep` only after `GO-LIVE-READY`

Do not send a strategy to deployment prep if this skill does not explicitly return `GO-LIVE-READY`.
