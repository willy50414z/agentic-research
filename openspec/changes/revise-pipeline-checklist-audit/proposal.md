## Why

`fix-loop-control-artifacts-and-summary` 上線後實測發現 4 個結構性問題：(1) `max_loops=2` 設定未生效、實際跑了 3 輪；(2) `v0_3_summary_report.md` 命名與實情不符；(3) `v{n}_is.zip` / `v{n}_oos.zip` 在 implement 與 analyze 兩個階段重複上傳；最關鍵的 (4) 三輪 backtest 績效完全相同——比對 v0/v2 策略 `.py` 為 byte-identical，因為 `revise_step` 只更新 `implementation_plan` dict，但 `_real_implement` 直接 `shutil.copy2` 原始 strategy 檔案，從未根據修訂參數重生 `.py`，導致整個 FAIL→REVISE→IMPLEMENT 迴圈失效。現有 2-LLM 修訂流程審核的是 JSON 字典、跟最終會被 freqtrade 執行的 `.py` 完全脫鉤。

## What Changes

- **REVISE pipeline 全面重設計（BREAKING）**：以 `intent → intent audit → checklist → subagent code-write → audit (deterministic + LLM)` 五階段取代現有 2-LLM JSON 驗證流程
  - LLM1 產 `revision_intent.md`（自然語言修訂方向）
  - LLM2 審核 intent，retry ≤ 2，超過則 TERMINATE 該輪
  - LLM2 將 approved intent 翻譯成 `checklist.yaml`（鎖定不可變更的修改契約）
  - Subagent 拿 checklist + 舊 `.py` 寫新 `.py`，並產出 `completion_report.yaml` 自報完成度
  - Audit 雙層：(a) Deterministic AST/regex check 處理 param 類項目與 invariants，(b) LLM3 audit 處理 logic 類項目
  - Audit fail → subagent 重寫，retry ≤ 2，超過則 TERMINATE
- **Strategy `.py` 每輪寫獨立檔案**：`artifacts/strategies/v{n}/{StrategyName}.py`，`plan.strategy_file` 對應更新；freqtrade `--strategy-path` 指向當輪隔離目錄，避免掃到舊版本
- **Strategy `.py` 採 staging path 生命週期**：Stage D subagent 寫到 `artifacts/.staging/v{n}/candidate.py`；audit 通過後 promote 到 `artifacts/strategies/v{n}/{StrategyName}.py`；audit fail TERMINATE 時 staging 保留供 forensics、永不 promote。避免後續 implement/backtest 誤吃未通過 audit 的版本
- **新增 feature flag `REVISE_PIPELINE_VERSION`**：v1（舊 2-LLM JSON 流程）/ v2（本 change 新流程）可漸進切換；預設值與 rollback 行為納入 spec requirement
- **修正 max_loops 同步時序**：`max_loops` 從 Planka card 讀取後必須在 `_build_state` 第一次讀取 cfg 之前完成寫入；增加 log assertion 驗證實際值
- **修正 summary 檔名**：以 `v{first_iter}_{last_iter}_summary_report.md` 取代 `v{loop_index}_{max_loops}_summary_report.md`，反映實際執行 iteration 範圍
- **去除 is/oos zip 重複上傳**：`_upload_new_artifacts` 過濾 `type ∈ {is_zip, oos_zip}`；只透過 `v{n}_backtest.zip` 統一上傳
- **新增每輪 strategy spec 快照**：每輪 revise 完成後輸出 `v{n}_strategy_spec.md`，記錄當輪策略名稱、參數、與前輪 delta，上傳 Planka

## Capabilities

### New Capabilities

- `revise-checklist-protocol`：定義 `checklist.yaml`、`completion_report.yaml`、`audit_report.yaml` 三份契約檔的 schema、欄位必填性、INSUFFICIENT/FAIL/REJECTED/UNIMPLEMENTABLE_CHECKLIST/IMPLEMENTATION_FAILED 語意；是 Stage C/D/E 的資料契約 source of truth
- `per-iteration-strategy-snapshot`：每輪 revise 後產出 `v{n}_strategy_spec.md`，內容**優先從通過 audit 的 `.py` 萃取**，LLM 僅可補充非結構性說明；提供完整的當輪策略快照（不僅是 delta description），並上傳至 Planka 作為 traceability artifact

### Modified Capabilities

- `fail-path-revise-plan`：FAIL 後的修訂流程從「2-LLM JSON 驗證」改為「intent → checklist → subagent → audit」五階段流程，並要求每輪寫獨立 `.py`；新增 retry 上限與 TERMINATE 條件
- `per-iteration-artifact-packaging`：`is_zip` / `oos_zip` 不再透過 `_upload_new_artifacts` 個別上傳，僅透過 `v{n}_backtest.zip` 統一上傳；artifact 命名仍以 `analyze_attempt` 為基礎
- `terminate-summary-report`：報告檔名改用實際執行的 iteration 範圍（`v{first}_{last}_summary_report.md`），不再使用永遠為 0 的 `loop_index`
- `loop-control-sync`：`max_loops` 從 Planka custom field 同步至 DB 的時序保證——必須在 dispatch 內第一次 `_build_state` 之前完成；新增 sanity-check log

## Impact

| 檔案 | 異動 |
|------|------|
| `app/freqtrade/steps.py` | `revise_step` 全面重寫；新增 `_write_strategy_per_iteration` 邏輯 |
| `app/freqtrade/audit.py` | 新增：deterministic AST check + LLM3 audit 調度 |
| `app/freqtrade/checklist.py` | 新增：checklist.yaml schema 解析、鎖定機制、completion_report 比對 |
| `app/prompts/freqtrade/revise_intent.txt` | 新增：LLM1 產出 intent.md 的 prompt |
| `app/prompts/freqtrade/revise_intent_audit.txt` | 新增：LLM2 審 intent 的 prompt |
| `app/prompts/freqtrade/revise_checklist.txt` | 新增：LLM2 翻譯 intent → checklist.yaml |
| `app/prompts/freqtrade/revise_subagent.txt` | 新增：subagent 拿 checklist 寫新 `.py` |
| `app/prompts/freqtrade/revise_audit.txt` | 新增：LLM3 audit logic items |
| `app/prompts/freqtrade/revise.txt` | 保留作 v1 fallback；待 v2 production 驗收完成後再移除 |
| `app/prompts/freqtrade/revise_validate.txt` | 保留作 v1 fallback；待 v2 production 驗收完成後再移除 |
| `app/workflow/executing_step.py` | `_run_revise` 改為調度新流程；`_run_terminate_summarize` 修正檔名邏輯；`_upload_new_artifacts` 過濾 is/oos zip |
| `app/api/server.py` | `max_loops` 同步流程加 log + assertion；確認時序正確 |
| `app/freqtrade/backtest.py` | `--strategy-path` 改用每輪隔離目錄 |
| `tests/freqtrade/test_revise_pipeline.py` | 新增：覆蓋 intent→checklist→subagent→audit 全流程的 unit tests |
| `tests/freqtrade/test_audit.py` | 新增：deterministic check 與 LLM3 audit 的單元測試 |

無 DB schema 變更；無 API breaking change；Planka custom field 不變。
