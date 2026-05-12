---
name: data-pipeline
description: Fetch, validate, and cache market data via BinanceService into the FeatureStore. Use for ML feature engineering and custom analysis — NOT for Freqtrade backtest data (use download-data skill for that).
---

# Data Pipeline Skill

> **Scope**: This skill covers the **FeatureStore** pipeline (`data/features/`) used by custom analysis scripts and ML pipelines.
> For Freqtrade backtest data, use the `download-data` skill instead (`freqtrade/user_data/data/binance/`).
> See `knowledge-base/skills/project-skills/trade-strategy.md` → Data Paths for the full path reference.

Covers the full lifecycle: fetch → validate → cache → use.

## When to Fetch New Data

Fetch when any of the following are true:
- The requested date range is not covered by the existing `.feature` cache.
- The cache file does not exist for the required exchange / market_type / product / timeframe combination.
- Data integrity check fails (see validation rules below).

Do **not** re-fetch if the existing cache fully covers `start_dt` to `end_dt` — `BinanceService` handles this automatically.

## Feature Cache Layout

Canonical path: `data/features/<EXCHANGE>/<MARKET_TYPE>/<PRODUCT>_<TIMEFRAME>.feature`

Examples:
- `data/features/BINANCE/FUTURE/BTCUSDT_1h.feature`
- `data/features/BINANCE/SPOT/ETHUSDT_15m.feature`

Files are in Apache Feather format (binary). Never edit them manually.

## Fetching Data

```bash
python -m lib.endpoints.get_crypto_exchange_data BTCUSDT FUTURE HOUR_1 \
  "2025-01-01T00:00:00+00:00" "2026-01-01T00:00:00+00:00"
```

Or call `BinanceService.get_kline(dto)` directly in Python.

## Validation Rules

After fetching, verify:

1. **No excessive gaps** — If missing candles exceed 0.1% of the expected total, re-fetch that sub-range.
2. **Timezone consistency** — All timestamps must be UTC-aware. `FeatureStore.localize_index_utc()` handles this.
3. **No future leak** — Signal computation at time `T` must only use data available at `T`. Never use `shift(-N)` on target labels.
4. **Sorted index** — The DataFrame index must be sorted ascending after load. `BinanceService` ensures this on save.

## Candle Count Sanity Check

```python
# Expected candles for 1h data over 365 days
expected = 365 * 24
actual = len(df)
gap_ratio = (expected - actual) / expected
if gap_ratio > 0.001:
    print(f"WARNING: {gap_ratio:.2%} of candles missing — consider re-fetch")
```

## Feature Cache Lifecycle

| Event | Action |
|-------|--------|
| First request for a range | Full API fetch, save to `.feature` |
| Partial cache (missing head/tail) | Gap-fill fetch, merge + save |
| Cache fully covers range | Return slice from cache, no API call |
| Cache file corrupted | Delete the `.feature` file and re-fetch |

## Code Reference

| Purpose | Path |
|---------|------|
| Fetch and cache klines | `lib/client/crypto_exchange/binance_svc.py` |
| Feature file persistence | `lib/storage/feature_store.py` |
| CLI fetch entry point | `lib/endpoints/get_crypto_exchange_data.py` |
| Kline criteria DTO | `lib/client/crypto_exchange/crypto_exchange_kline_criteria_dto.py` |
| Data validation rules | `knowledge-base/skills/domain-skills/trading/trade-strategy-data-validation/SKILL.md` |
