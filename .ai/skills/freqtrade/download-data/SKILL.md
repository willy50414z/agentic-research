---
name: freqtrade-download-data
description: Download OHLCV market data for Freqtrade backtesting. Use when data-readiness-check fails or when new data is needed for a specific timerange.
---

# Freqtrade Download Data Skill

## Quick Reference

Use the `freqtrade` endpoint with `--mode download-data` to fetch OHLCV data from exchanges (default: Binance).

```bash
python -m lib.endpoints.freqtrade <timerange> --mode download-data --pairs <pairs> --timeframes <timeframes>
```

### Examples

**Download BTC/USDT Futures 15m data:**
```bash
python -m lib.endpoints.freqtrade 20240101-20241231 --mode download-data --pairs BTC/USDT:USDT --timeframes 15m
```

**Download Multiple Pairs and Timeframes:**
```bash
python -m lib.endpoints.freqtrade 20240101-20241231 --mode download-data \
  --pairs BTC/USDT:USDT ETH/USDT:USDT \
  --timeframes 5m 15m 1h
```

**Download Spot Data:**
```bash
python -m lib.endpoints.freqtrade 20240101-20241231 --mode download-data \
  --pairs BTC/USDT ETH/USDT \
  --trading-mode spot
```

---

## Parameters

| Parameter | Description | Default |
|-----------|-------------|---------|
| `timerange` | Period to download (YYYYMMDD-YYYYMMDD) | (Required) |
| `--pairs` | List of pairs (Space separated) | `BTC/USDT:USDT` |
| `--timeframes` | List of timeframes (Space separated) | `15m` |
| `--exchange` | Exchange name | `binance` |
| `--trading-mode` | `spot` or `futures` | `futures` |

---

## When to Use

1.  **Before Backtesting**: Run `data-readiness-check` first. If it returns `MISSING` or `TRUNCATED`, use this skill to fetch the required data.
2.  **New Pair/Timeframe**: When a strategy requires a pair or timeframe that hasn't been downloaded yet.
3.  **Updating Data**: To extend the historical data to the most recent available date.

---

## Data Location

Downloaded data is stored in:
`freqtrade/user_data/data/<exchange>/`

Freqtrade backtesting will automatically look for data in this directory based on the configuration.

---

## Workflow Integration

1.  **Check**: `python -m lib.endpoints.check_data_readiness ...`
2.  **Fetch (if needed)**: `python -m lib.endpoints.freqtrade ... --mode download-data ...`
3.  **Verify**: Re-run `check_data_readiness` to ensure the data is now present and covers the range.
4.  **Backtest**: `python -m lib.endpoints.freqtrade ... --mode backtesting`

---

## Code Reference

| Purpose | Path |
|---------|------|
| CLI Entry point | `lib/endpoints/freqtrade.py` |
| Execution Adapter | `lib/strategy/execution/freqtrade_executor.py` |
| Freqtrade Data Directory | `freqtrade/user_data/data/` |
