---
name: trade-strategy-data-validation
description: Collect and validate trading datasets while checking leakage, time boundaries, missing candles, and split integrity. Use when preparing market data, validating time-series datasets, or reviewing dataset readiness for strategy research and backtesting.
---

# Trade Strategy Data Validation Skill

Collect and validate historical data while preventing leakage.

## Core Standards

- **Leakage Prevention**: Mandatory check for Look-ahead bias in OHLCV alignment.

- Store raw and derived datasets in a consistent location defined by the host repository.

## Required Work

- Ensure timerange covers at least one full bull and one full bear cycle.
- **Point-in-time check**: Verify that signals at time `T` do not use information from `T+1` (e.g., future close prices).
- **Split Strategy**: Define Train/Validation/OOS (Out-of-Sample) boundaries.
- **Integrity**: Check for missing candles; if > 0.1% are missing, perform interpolation or data re-fetch.
