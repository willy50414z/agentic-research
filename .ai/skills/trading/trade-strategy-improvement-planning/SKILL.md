---
name: trade-strategy-improvement-planning
description: Build the next strategy iteration plan from observed weaknesses, failure modes, and audit results. Use when deciding how to improve a strategy after backtest review, report analysis, or failed validation.
---

# Trade Strategy Improvement Planning Skill

Refine trading strategies based on historical audit results.

## MANDATORY PREREQUISITES

Before running improvement planning, confirm all of the following:

1. **`experiment-compare`** must be run first if this is the second iteration or later.
2. **`knowledge-extraction`** must be run if `experiment-compare` verdict is KEEP.
3. **`sessions/<date>-analysis-summary.md`** must already exist.

If any prerequisite is missing, run that step first and return here.

> Skipped/N/A with justification: first iteration has no prior experiment to compare. State "First iteration — no experiment-compare required" in the iteration plan.

---

## Quick Reference

For simple planning tasks — stop reading after this section if covered.

| Step | Action |
|------|--------|
| 1. Load context | Read `STATUS.md` frontmatter, then `sessions/_latest.json` to find latest analysis summary |
| 2. Read summary | Read the file named in `_latest.json["analysis_summary"]` |
| 3. Check prior art | Read `hypothesis-log.md` — confirm the proposed change was not already tried |
| 4. Write plan | `sessions/<YYYYMMDD>-iteration-plan.md` |
| 5. Update pointers | `sessions/_latest.json["iteration_plan"]`, STATUS.md frontmatter: `phase: implementation` |
| 6. Update hypothesis log | Add new v<N> entry to `hypothesis-log.md` with PENDING verdict; refresh Quick Summary at top |

---

## Input

Read in this order before planning:
1. `strategies/<family>/STATUS.md` — read YAML frontmatter first for current phase and last stem
2. `strategies/<family>/sessions/_latest.json` — resolve the filename of the latest analysis summary
3. `strategies/<family>/sessions/<latest>-analysis-summary.md` — top failure modes and recommended focus
4. `strategies/<family>/hypothesis-log.md` — read the **Quick Summary section at the top** first (3–5 lines: anchor, last tested, open questions). If no summary exists, read the full file and add one after planning.
5. `strategies/<family>/README.md` — current hypothesis and spec (update this if the hypothesis changes)

Do not re-read the full analysis zip or HTML report unless the summary is missing.

## Required Work

- Identify the specific regime (e.g., Sideways, High Vol) where the strategy underperforms.
- Formulate a new hypothesis that addresses the identified failure mode.
- Update `strategies/<family>/README.md` directly if the hypothesis or spec changes — README.md is the single spec file.
- Write the iteration plan (see template below) before starting re-implementation.

## General Decision Rules

- Do not propose changes that are not linked to a specific observed weakness or failure mode.
- Do not batch unrelated improvements into one iteration — attribution becomes unclear.
- Do not re-implement before the revised hypothesis and iteration plan are written.
- Prefer one focused change per iteration. If the plan has more than two `Change` items, split into separate iterations.

## Iteration Plan Template

Write to: `strategies/<family>/sessions/<YYYYMMDD>-iteration-plan.md`

```markdown
# Iteration Plan — <YYYY-MM-DD>

## Source
- Based on: strategies/<family>/sessions/<date>-analysis-summary.md

## Prior Art Check
- Has this change been tried before? (check hypothesis-log.md)
  - If yes: cite version and outcome. Explain why re-testing is justified.
  - If no: state "No prior art found in hypothesis-log.md"

## Observed Weakness
<具體 regime 或 metric，例：Sideways market 造成 -12% profit drag>

## Root Cause Hypothesis
<為什麼這個 weakness 存在，對應 README.md 的哪個假設>

## Change
<改什麼，要具體到 indicator / parameter / logic>

## Unchanged
<明確列出不改的部分，避免 agent 自行擴題>

## Expected Metric Shift
- Profit Factor: X.X → X.X
- Max Drawdown: XX% → XX%

## Experiment Type
<!-- parameter tweak | logic change | filter addition | regime filter -->

## Test Plan
- IS timerange: <YYYYMMDD-YYYYMMDD>
- OOS timerange: <YYYYMMDD-YYYYMMDD>
```

After writing the plan:
1. Update `STATUS.md` frontmatter: `phase: implementation`, `next_action`, `last_updated`
2. Update `sessions/_latest.json`: set `"iteration_plan"` to the new filename
3. Add a new entry to `hypothesis-log.md` with verdict PENDING
4. Refresh the **Quick Summary** at the top of `hypothesis-log.md`:

```markdown
## Quick Summary (prior art index)
- Anchor: <version> (<metric, e.g. OOS PF 1.42>)
- Last tested: <version> (<verdict: KEEP/REVERT>, <key metric>)
- Pending: <current version> (<one-line change description>)
- Open question: <untested hypothesis worth exploring next>
```
