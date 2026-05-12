---
name: backtest-preflight
description: Composite pre-backtest gate. Runs data readiness, look-ahead bias check, and config completeness in sequence. Use this as the single entry point before every backtest instead of invoking each check separately.
---

# Backtest Preflight Skill

Consolidates the three mandatory pre-backtest gates into one checklist.
Run this every time before `python -m lib.endpoints.freqtrade ... --mode backtest`.

## Quick Reference

```
Preflight = data-readiness-check → look-ahead-bias-check → config-completeness-check
All three must PASS before running backtest.
```

---

## Gate 1 — Data Readiness

```bash
python -m lib.endpoints.check_data_readiness <pair> <timeframe> <timerange>
```

- Exit code `0` (READY) → pass to Gate 2
- Any other exit code → fix data first; see `data-readiness-check/SKILL.md`

**Also verify freshness** (Step 3a in data-readiness-check):
Feature cache end_date must be ≥ requested timerange end_date.

---

## Gate 2 — Look-Ahead Bias Check

Run only when:
- This is the **first backtest** after a new implementation, OR
- The strategy code was modified since the last clean check

```
Load: framework-skills/freqtrade/look-ahead-bias-check/SKILL.md
Verdict must be: CLEAN
```

If CLEAN verdict is already on file for the current strategy version, this gate passes without re-running.

---

## Gate 3 — Config Completeness

Verify `strategies/<family>/engine/freqtrade/config.json` is not a template placeholder and contains:

- [ ] `trading_mode` field present (e.g., `"futures"` or `"spot"`)
- [ ] `exchange` block present with `name` and `fee`
- [ ] `stake_amount` defined
- [ ] Not a raw placeholder (file size > 100 bytes, no `<TODO>` markers)

If config is missing or incomplete:
```bash
python -m lib.endpoints.generate_freqtrade_config <strategy_family>
```
Then re-verify the three fields above.

---

## Preflight Completion Checklist

- [ ] Gate 1: Data readiness READY + freshness verified
- [ ] Gate 2: Look-ahead bias CLEAN (or documented waiver with reason)
- [ ] Gate 3: config.json complete (no placeholders, has `trading_mode` + `exchange` + `stake_amount`)

All three PASS → proceed to `freqtrade-analysis-workflow` Phase 1.

---

## When to Skip (with justification)

| Gate | May skip when |
|------|--------------|
| Gate 1 | Data was checked within same session, timerange unchanged |
| Gate 2 | Strategy code unchanged since last CLEAN verdict |
| Gate 3 | Config was just generated and verified in same session |

Always document skipped gates with reason in session log.

---

## Code Reference

| Purpose | Path |
|---------|------|
| Data readiness CLI | `lib/endpoints/check_data_readiness.py` |
| Config generator | `lib/endpoints/generate_freqtrade_config.py` |
| Look-ahead bias skill | `framework-skills/freqtrade/look-ahead-bias-check/SKILL.md` |
| Base config template | `freqtrade/configs/base.json` |
| Strategy-local config | `strategies/<family>/engine/freqtrade/config.json` |
