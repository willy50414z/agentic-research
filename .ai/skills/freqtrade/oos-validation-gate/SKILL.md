---
name: oos-validation-gate
description: Enforce OOS (out-of-sample) validation as a hard gate before the Planning phase. Use after analysis to determine whether results are stable enough to proceed with strategy refinement, or whether the strategy must return to implementation.
---

# OOS Validation Gate Skill

OOS validation is the primary defense against overfitting. Without a hard gate, the iteration loop can produce a sequence of IS-optimized strategies that all fail on unseen data.

## Quick Reference

This gate is MANDATORY before writing an iteration plan. Apply after every backtest analysis.

| Condition | Verdict | Action |
|-----------|---------|--------|
| OOS Profit Factor ≥ 1.0 AND OOS/IS ratio ≥ 0.5 | PASS | Proceed to Planning phase |
| OOS Profit Factor ≥ 1.0 AND OOS/IS ratio < 0.5 | WARN | Proceed with mandatory overfitting note in iteration plan |
| OOS Profit Factor < 1.0 | BLOCK | Return to Implementation phase; do not write iteration plan |
| OOS trade count < 30 | INSUFFICIENT DATA | Extend backtest period before concluding |

---

## Workflow

### Step 1 — Identify IS and OOS periods

From the strategy spec (README.md) or iteration plan:
- IS (in-sample) period: the training/optimization window
- OOS (out-of-sample) period: the validation window that was not used in any optimization

The OOS period must be defined BEFORE any optimization or parameter tuning. If it was not pre-defined, do not label any period as OOS — it is all IS.

### Step 2 — Extract OOS metrics from the analysis output

From `freqtrade/reports/<stem>_<strategy>.zip` or the analysis summary, extract:
- IS Profit Factor
- OOS Profit Factor (from the validation timerange)
- Total trades in OOS period

If OOS was not run separately, request a second backtest run on the OOS period before proceeding.

### Step 3 — Apply gate rules

```
OOS_PF = OOS Profit Factor
IS_PF  = IS Profit Factor
RATIO  = OOS_PF / IS_PF

IF OOS_PF < 1.0:
    → BLOCK: strategy is losing on unseen data
    → Phase stays at 'analysis' or returns to 'implementation'
    → Write a blocking note in STATUS.md: why this failed OOS

IF OOS_PF ≥ 1.0 AND RATIO < 0.5:
    → WARN: OOS significantly underperforms IS (likely overfitting)
    → Proceed to Planning ONLY if the iteration plan explicitly addresses:
      - Why the OOS/IS gap is acceptable
      - What changes will reduce overfitting in the next iteration

IF OOS_PF ≥ 1.0 AND RATIO ≥ 0.5:
    → PASS: proceed to Planning phase

IF OOS trade count < 30:
    → INSUFFICIENT DATA: extend the OOS period before concluding
```

### Step 4 — Record verdict in STATUS.md

Update STATUS.md frontmatter and human-readable section:

For BLOCK:
```
phase: implementation  # or analysis if still investigating
next_action: "OOS failed (PF <value>). Return to implementation to address overfitting."
```

For WARN/PASS:
```
phase: planning
next_action: "OOS passed. Write iteration plan. [Note OOS/IS ratio if WARN]"
```

### Step 5 — Update analysis summary gate section

Add to `sessions/<date>-analysis-summary.md`:

```markdown
## OOS Validation Gate

- IS Profit Factor: X.XX
- OOS Profit Factor: X.XX
- OOS/IS Ratio: X.XX
- OOS Trade Count: N
- Verdict: PASS | WARN | BLOCK | INSUFFICIENT DATA
- Note: <reason if WARN or BLOCK>
```

---

## Why This Gate Matters

Without a hard OOS gate:
- A strategy can pass 10 consecutive IS backtests with Profit Factor > 2.0
- Every iteration looks like an improvement
- The first live trade reveals the strategy only works on the training data

The gate forces honest evaluation. If OOS consistently fails, the hypothesis is likely wrong — not the parameters.
