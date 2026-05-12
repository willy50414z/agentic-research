---
name: freqtrade-analysis-workflow
description: Run, validate, analyze, and tune Freqtrade backtest results in one workflow. Covers backtest execution, OOS validation, performance diagnosis, and hyperopt. Use this instead of the individual backtest-validation, performance-analysis, and parameter-tuning skills.
---

# Freqtrade Analysis Workflow Skill

Covers the full post-implementation loop: backtest → validate → analyze → tune.

## Quick Reference

For common operations — stop reading after this section if the task is covered here.

| Operation | Command | Required before running |
|-----------|---------|------------------------|
| Check data readiness | `python -m lib.endpoints.check_data_readiness <pair> <timeframe> <timerange>` | Always do this before backtest |
| Run backtest | `python -m lib.endpoints.freqtrade <family> <StrategyName> <timerange>` | config.json + strategy file exist + data READY |
| Run analysis | `python -m lib.strategy.analytics.analyze_backtest_result` | backtest stem.json exists |
| After backtest | Update `STATUS.md` frontmatter: phase → `analysis`, fill `last_backtest_stem` | — |
| After analysis | Write `sessions/<date>-analysis-summary.md`, update `sessions/_latest.json`, STATUS.md phase → `planning` | — |
| Hyperopt | `python -m lib.endpoints.freqtrade <family> <StrategyName> <timerange> --mode hyperopt --epochs 200` | Stable baseline (PF > 1.0 OOS) |

---

## Pre-flight (before starting any phase)

- `strategies/<family>/README.md` is the single source of truth for strategy spec. All work traces back to it.
- `strategies/<family>/STATUS.md` shows the current phase and last backtest stem. Read it first.

---

## Phase 1 — Run Backtest

**Before running — execute `backtest-preflight` skill first:**
See: `framework-skills/freqtrade/backtest-preflight/SKILL.md`

Preflight covers three gates in sequence: data readiness → look-ahead bias check → config completeness. All three must PASS before proceeding.

```bash
python -m lib.endpoints.freqtrade <strategy_family> <StrategyName> <timerange> --mode backtest
```

**Additional gate — confirm before running:**
- [ ] Backtest-preflight all three gates PASS (see above)
- [ ] `freqtrade/strategies/<StrategyName>.py` exists with no TODO markers
- [ ] `python -m lib.endpoints.freqtrade` does not raise an import error

**After running:**
- Update `STATUS.md` YAML frontmatter only: `phase: analysis`, `last_backtest_stem`, `last_run_metrics`
- Do not add prose to STATUS.md — narrative belongs in `sessions/<YYYYMMDD>-status-note.md` if needed.

### PHASE TRANSITION GATE — Backtest → Analysis

All items must be complete before moving to Phase 2. If any item is missing, this phase is **INCOMPLETE**.

- [ ] `STATUS.md` frontmatter updated: `phase: analysis`
- [ ] `STATUS.md` `last_backtest_stem` filled with the new stem
- [ ] `STATUS.md` `last_run_metrics` filled (PF, DD, WR, Sharpe)
- [ ] Backtest result exists: `freqtrade/user_data/backtest_results/<stem>.json`

> Skipped/N/A with justification: if the backtest was intentionally aborted (e.g., data error), document the reason in STATUS.md `next_action` and do not advance the phase.

---

## Phase 2 — Validate Results

**Mandatory checks (run on every backtest, regardless of metrics):**

- [ ] **Look-ahead bias check** — run `look-ahead-bias-check` skill on the strategy file. Verdict must be CLEAN before proceeding. See: `framework-skills/freqtrade/look-ahead-bias-check/SKILL.md`
- [ ] **OOS validation gate** — run `oos-validation-gate` skill to enforce hard OOS threshold. Verdict must be PASS or WARN before writing an iteration plan. See: `framework-skills/freqtrade/oos-validation-gate/SKILL.md`

| Threshold check | Action if breached |
|----------------|-------------------|
| OOS Profit Factor < 1.0 | BLOCK Planning phase — return to Implementation |
| OOS/IS ratio < 0.5 | WARN — iteration plan must address overfitting |
| Drawdown recovery > 30 days | Unstable — revise risk controls |
| Slippage 0.1% makes strategy unprofitable | Fragile — fix edge quality |

---

## Phase 3 — Analyze Performance

```bash
python -m lib.strategy.analytics.analyze_backtest_result
```

Diagnose in this order:
1. **Signal Win Rate vs Trade Win Rate** — if trade WR ≪ signal WR, exit logic is cutting winners early
2. **Gate Filter Funnel** (`dbg_` columns) — check if any filter blocks profitable trades (false positives)
3. **Slippage Sensitivity** — strategy must remain profitable at 0.1% slippage per side

**Gate — required before next iteration:**
Write the analysis summary at: `strategies/<family>/sessions/<YYYYMMDD>-analysis-summary.md`
Update `strategies/<family>/sessions/_latest.json`: set `"analysis_summary"` to the new filename and `"last_updated"` to today.

Use the following fixed section schema. Sections are labelled so agents can load only what they need:

```markdown
# Analysis Summary — <stem> — <YYYY-MM-DD>

<!-- KEY_METRICS: always load — minimum context for any follow-up task -->
## KEY_METRICS
- Profit Factor (IS): X.XX
- Profit Factor (OOS): X.XX
- Max Drawdown: XX%
- Win Rate: XX%
- Sharpe: X.XX
- Slippage 0.1% impact: -X.XX PF
- OOS/IS ratio: X.XX

<!-- FAILURE_MODE: load when planning next iteration -->
## FAILURE_MODE
1. <regime/condition>: <metric impact>
2. <regime/condition>: <metric impact>

## Recommended Focus for Next Iteration
<一到兩句具體建議，指向 README.md 裡的哪個 spec 部分需要更新>

<!-- REGIME_ANALYSIS: load when working on regime filter -->
## REGIME_ANALYSIS
- Bull: trades=<N>, WR=<X>%, PF=<X.XX>
- Bear: trades=<N>, WR=<X>%, PF=<X.XX>
- Sideways: trades=<N>, WR=<X>%, PF=<X.XX>

<!-- FULL_DIAGNOSTICS: load only when debugging specific signal or exit issues -->
## FULL_DIAGNOSTICS
- Signal WR vs Trade WR: <signal WR>% → <trade WR>% (delta: <X>pp)
- Gate filter funnel: <dbg_ column analysis>
- Slippage sensitivity table: <0%, 0.05%, 0.1%, 0.2%>
```

**Section loading guide for agents:**

| Task | Load sections |
|------|--------------|
| Planning next iteration | KEY_METRICS + FAILURE_MODE |
| Regime filter tuning | KEY_METRICS + REGIME_ANALYSIS |
| Debugging signal/exit | KEY_METRICS + FULL_DIAGNOSTICS |
| Cold-start status check | KEY_METRICS only |

Update `STATUS.md`: phase → `planning`.

### PHASE TRANSITION GATE — Analysis → Planning

All items must be complete before moving to Planning. If any item is missing, this phase is **INCOMPLETE**.

- [ ] `sessions/<YYYYMMDD>-analysis-summary.md` written (format above)
- [ ] `sessions/_latest.json` updated: `analysis_summary` + `last_updated`
- [ ] `STATUS.md` frontmatter: `phase: planning`
- [ ] OOS validation gate passed or documented as accepted risk (Phase 2)

> Skipped/N/A with justification: if this is a debug run (no analysis summary needed), document in STATUS.md `next_action` and keep phase as `analysis`.

---

## Phase 4 — Parameter Tuning (Hyperopt)

**Only proceed if:**
- Stable baseline exists (Profit Factor > 1.0 on OOS)
- OOS validation path is defined before tuning begins

```bash
python -m lib.endpoints.freqtrade <family> <StrategyName> <timerange> --mode hyperopt --epochs 200
```

Tuning rules:
- Target **Sortino Ratio** or **Sharpe Ratio** — not raw Total Profit
- Inspect the "Best 10" results: if parameter sets are too diverse, strategy logic is unstable — stop and fix logic first
- Stop tuning if improvements only appear in isolated parameter pockets with no nearby stability zone
- Record tuning results in `strategies/<family>/experiments/<YYYYMMDD>-hyperopt-<topic>.md`
- Re-test final parameters on a clean OOS dataset before accepting

**After tuning:**
Update `STATUS.md`: phase → `planning` (if done) or back to `backtest` (if re-running).

---

## Phase Gate Summary

| Phase | Done when |
|-------|-----------|
| Backtest | `freqtrade/user_data/backtest_results/<stem>.json` exists; STATUS.md updated |
| Validate | All threshold checks passed or documented as accepted risks |
| Analyze | `sessions/<date>-analysis-summary.md` written; STATUS.md → `planning` |
| Hyperopt | Final params OOS-validated; experiment record written |

---

## Code Reference

| Purpose | Path |
|---------|------|
| Run backtest / hyperopt | `lib/endpoints/freqtrade.py` |
| Freqtrade execution adapter | `lib/strategy/execution/freqtrade_executor.py` |
| Backtest result artifacts | `freqtrade/user_data/backtest_results/` |
| Hyperopt results | `freqtrade/user_data/hyperopt_results/` |
| Analysis script | `lib/strategy/analytics/analyze_backtest_result.py` |
| Generated analysis reports | `freqtrade/reports/` |
| Strategy-local experiment records | `strategies/<family>/experiments/` |
| Cross-strategy knowledge | `knowledge-base/knowledge/backtest-records/` |
