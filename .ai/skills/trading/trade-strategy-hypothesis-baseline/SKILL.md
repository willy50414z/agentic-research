---
name: trade-strategy-hypothesis-baseline
description: Define trading hypotheses and comparison baselines that can be falsified and tested fairly. Use when turning a trading idea into a testable strategy thesis or when deciding what baseline a strategy must beat.
---

# Trade Strategy Hypothesis Baseline Skill

Define testable trading ideas and comparison baselines.

## Core Standards

- **Baseline Requirement**: Every strategy MUST be compared against a "Buy and Hold" and a "Simple Trend Follower" (e.g., SMA 20/50 Cross).

## Required Work

- Document the core alpha logic: Why does this strategy make money?
- Define **Hard Baseline**: The strategy must outperform the baseline by at least 20% in net profit with lower drawdown.
- If ML is used, the baseline must be the non-ML version of the same logic.

## General Decision Rules

- Do not proceed if the alpha logic cannot be stated as a falsifiable market hypothesis.
- Do not proceed if the baseline is so weak that beating it does not establish real trading value.
- If ML is used, do not compare only against unrelated baselines; at least one baseline must isolate whether ML adds value over the same underlying logic.
