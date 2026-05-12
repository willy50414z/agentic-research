---
name: data-readiness-check
description: Verify that Freqtrade backtest data exists and covers the full requested timerange before running a backtest. Use as the first gate in the backtest preflight.
---

# Data Readiness Check Skill

## Quick Reference

```bash
python -m lib.endpoints.check_data_readiness <pair> <timeframe> <timerange>
```

Exit codes:
- `0` — READY: proceed with backtest
- `1` — MISSING: data file not found; fetch data first
- `2` — TRUNCATED: data exists but doesn't cover full range; fetch more data
- `3` — ERROR: file corrupt or unreadable

> **Data path reference**: See `knowledge-base/skills/project-skills/trade-strategy.md` → Data Paths section.

---

## When to Use

Always run before `python -m lib.endpoints.freqtrade ... --mode backtesting`.

This check catches two silent failure modes:
1. **MISSING**: Freqtrade will fail with a cryptic error or produce zero trades
2. **TRUNCATED**: Freqtrade silently backtests a shorter period, making results misleading

---

## Workflow

### Step 1 — Identify parameters

From `strategies/<family>/README.md`:
- `pair`: Freqtrade product identifier (e.g. `BTCUSDT`)
- `timeframe`: candle period (e.g. `15m`)
- `timerange`: backtest period in YYYYMMDD-YYYYMMDD format

Check **both** the execution timeframe and any informative timeframes (e.g. 1h HTF).

### Step 2 — Run check

```bash
# Execution timeframe
python -m lib.endpoints.check_data_readiness BTCUSDT 15m 20230101-20241231

# Informative timeframe (if strategy uses HTF)
python -m lib.endpoints.check_data_readiness BTCUSDT 1h 20230101-20241231
```

### Step 3 — Handle result

| Status | Action |
|--------|--------|
| `READY` | Proceed to freshness check (Step 3a) |
| `MISSING` | Download data with `download-data` skill, then re-check |
| `TRUNCATED` | Download missing range with `download-data` skill, then re-check |
| `ERROR` | Inspect the file manually; re-download if corrupted |

### Step 3a — Freshness check

Verify data end_date >= requested timerange end_date. If stale, treat as TRUNCATED.

### Step 4 — On MISSING or TRUNCATED

Use `download-data` skill:
- `knowledge-base/skills/framework-skills/freqtrade/download-data/SKILL.md`

After downloading, re-run the check to confirm READY.

---

## Code Reference

| Purpose | Path |
|---------|------|
| CLI endpoint | `lib/endpoints/check_data_readiness.py` |
| Freqtrade data directory | `freqtrade/user_data/data/binance/` |
| Data path reference (single source of truth) | `knowledge-base/skills/project-skills/trade-strategy.md` → Data Paths |
