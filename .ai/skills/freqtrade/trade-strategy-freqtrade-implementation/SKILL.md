---
name: trade-strategy-freqtrade-implementation
description: Implement trading strategies in Freqtrade with auditable signals, reproducible config, and traceable mapping from prototype to code. Use when building or reviewing a Freqtrade strategy implementation.
---

# Trade Strategy Freqtrade Implementation Skill

Implement strategies in Freqtrade with auditable, reproducible behavior.

## Quick Reference

For common implementation tasks — stop reading after this section if covered.

**Required methods checklist:**
- [ ] `populate_indicators` — all indicators computed; `dbg_` columns included for all filter signals
- [ ] `populate_entry_trend` — uses only lagged signals (no `.shift(0)` on future data)
- [ ] `populate_exit_trend` — traceable to prototype spec

**Common look-ahead bias traps (cross-ref `look-ahead-bias-check`):**

| Pattern | Issue | Fix |
|---------|-------|-----|
| `df['signal'].shift(0)` in entry | Future leak | Use `.shift(1)` |
| `.iloc[-1]` in `custom_exit` | Future leak | Use last-row column values |
| Informative merge without `ffill` | Gap fill issues | Always `ffill` after merge |

**Config generation (run after implementation):**
```bash
python -m lib.endpoints.generate_freqtrade_config <strategy_family>
```
Verify `config.json` has `trading_mode`, `exchange`, and `fee` fields before backtest.

---

## Framework Standards

- Keep strategy files, config, and dependencies organized so the strategy can be run reproducibly in the target repo.
- Use the host repository's naming and import conventions instead of assuming a fixed package layout.

## Required Work

- Implement `populate_indicators`, `populate_entry_trend`, and `populate_exit_trend`.
- Include `dbg_` columns for all internal indicators to support `analyze_backtest_result.py`.
- Manage `informative_pairs` if required for multi-timeframe analysis.
- Update the strategy-local Freqtrade config with appropriate whitelist, stake amount, and dry-run wallet settings.

## Implementation Completion Checklist

Run this before marking implementation as done and advancing to `backtest` phase:

- [ ] `populate_indicators`, `populate_entry_trend`, `populate_exit_trend` all implemented
- [ ] `dbg_` columns included for all filter signals in `populate_indicators`
- [ ] Look-ahead bias self-check passed (cross-ref `look-ahead-bias-check/SKILL.md`)
- [ ] Config generated and verified:
  ```bash
  python -m lib.endpoints.generate_freqtrade_config <strategy_family>
  ```
  Confirm `strategies/<family>/engine/freqtrade/config.json` has `trading_mode`, `exchange`, and `stake_amount` — not a template placeholder.
- [ ] `freqtrade/strategies/<StrategyName>.py` importable: `python -m lib.endpoints.freqtrade` does not raise an import error
- [ ] `STATUS.md` phase updated to `backtest`

---

## General Decision Rules

- Do not mark the implementation as complete if the Freqtrade logic cannot be traced back to the approved prototype or research hypothesis.
- Do not proceed if entry, exit, or risk-control behavior depends on hidden state that cannot be audited from the dataframe or strategy code.
- Do not proceed if informative timeframe handling, config assumptions, or runtime dependencies differ materially from the intended live setup.
- Do not proceed if the strategy cannot be executed reproducibly with the documented strategy file, config, and required artifacts.

## Code Reference

| Purpose | Path |
|---------|------|
| Run backtesting / hyperopt | `lib/endpoints/freqtrade.py` |
| Freqtrade execution adapter | `lib/strategy/execution/freqtrade_executor.py` |
| Generate strategy config | `lib/endpoints/generate_freqtrade_config.py` |
| Base Freqtrade config template | `freqtrade/configs/base.json` |
| Strategy .py file location | `freqtrade/strategies/<StrategyClass>.py` |
| Strategy-local config | `strategies/<family>/engine/freqtrade/config.json` |
| Technical indicators (TA-Lib wrapper) | `lib/ohlcv_data_handler/tech_idx_svc.py` |

---

## 每輪策略 `.py` 目錄佈局（agentic-research v2 revise pipeline）

當 agentic-research 框架以 `REVISE_PIPELINE_VERSION=v2` 流程執行時，每一輪 iteration N 的策略 `.py` 會落在獨立目錄，避免 freqtrade `--strategy-path` 掃到舊版本的 class。

### 正式路徑（promoted artifacts）

```
artifacts/strategies/v0/{StrategyName}.py   ← baseline，由 plan_step 直接寫入（無 staging）
artifacts/strategies/v1/{StrategyName}.py   ← 第 1 輪 revise 通過 audit 後 promote
artifacts/strategies/v2/{StrategyName}.py   ← 第 2 輪 revise 通過 audit 後 promote
...
```

每個 `v{N}/` 目錄保證僅含當輪一份 `.py` 檔案；freqtrade backtest 的 `--strategy-path` 必須指向當輪目錄而非根目錄。

### Staging 路徑（Stage D 寫入暫存）

Revise 流程 Stage D 期間，subagent 寫出的候選 `.py` 落在 staging：

```
artifacts/.staging/v{N}/candidate.py
artifacts/.staging/v{N}/checklist_attempt_{k}.yaml
artifacts/.staging/v{N}/completion_report_attempt_{k}.yaml
artifacts/.staging/v{N}/audit_report_attempt_{k}.yaml
```

只有 audit `overall: APPROVED` 的候選才會被 atomic promote 到 `artifacts/strategies/v{N}/{StrategyName}.py`；audit 從未 APPROVED 而 TERMINATE 時，`artifacts/strategies/v{N}/` 不會建立、staging 完整保留供 forensics。

### 每輪策略快照 `v{N}_strategy_spec.md`

每輪 promote 完成後，框架會在 `artifacts/` 同時產出 `v{N}_strategy_spec.md` 並上傳 Planka：

- 結構性內容（class name、timeframe、stoploss、minimal_roi、hyperopt parameter default、進出場條件）由 deterministic AST 解析從通過 audit 的 `.py` 萃取
- LLM 僅可補充自然語言修訂摘要與 delta 描述，不得生成或推測任何參數值
- baseline `v0_strategy_spec.md` 由 `plan_step` 產出，無 delta 區段

### 對策略開發者的影響

- 不要在多輪之間共用 `.py`；每輪都是獨立 class（class name 沿用，但檔案位置不同）
- 不要直接修改 `artifacts/.staging/`；該目錄是 audit 失敗時的證據保留區
- 若要追蹤策略演進，從 `v{N}_strategy_spec.md` 的 delta 區段或比對 `artifacts/strategies/v{N-1}/` vs `artifacts/strategies/v{N}/` 的 `.py` 著手
