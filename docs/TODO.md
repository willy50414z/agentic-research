## 下次確認（來自 executing_step 重構，2026-04-30）

- [x] **`loop_index` vs `attempt_index` 語意確認**：已確認兩者用途不同。`loop_index` 是 freqtrade steps 的外層迴圈計數（`summarize_step` 會 +1 回傳），用於檔案命名（`loop_0_train.json`）；`attempt_index` 是 `executing_step` 在 DB 端追蹤的 analyze 呼叫次數。兩者皆需保留。
- [x] **Step/result 字串改用 Enum**：已建立 `app/workflow/constants.py`，定義 `WorkflowStep`、`AnalysisResult`、`PlankaColumn` 三個 `str Enum`，`executing_step.py` 已全面套用。
- [x] **`sink` 缺乏 Protocol 型別標注**：已在 `app/workflow/constants.py` 定義 `WorkflowSink(Protocol)`，`executing_step.py` 的 `sink` 參數已標注型別。
- [x] **`dispatch_step` 兩次 DB round-trip**：已更新 `queries.get_project` 同時 SELECT `workflow_step`，`dispatch_step` 改從 `project["workflow_step"]` 取值，不再呼叫 `get_workflow_step`。
- [x] **State 中 `config` 欄位與個別展開欄位並存**：已從 `_build_state` 移除 `"config": cfg`，改展開 `paused_at` 為 top-level key；`_run_implement` 改讀 `state.get("paused_at")`，`_MERGE_EXCLUDED` 同步移除 `"config"` 改排除 `"paused_at"`。
- [x] **Artifact 上傳邏輯重複**：確認 `spec_review_step._upload_work_dir` 是批量目錄上傳，`executing_step._run_summarize` 是單一特定檔案上傳，語意不同，不抽共用。
- [ ] **`DEV_CHECKLIST.md` 路徑全面過期**：大量引用已刪除的 `framework/` 目錄。需對照新 `app/` 結構重寫（用戶已指示 md 先不改）。

---

## 舊有待辦

- [ ] `app/freqtrade/steps.py` backtest 目前為 stub，需接真實 Freqtrade CLI（參考 `E:\code\binance\...freqtrade_backtest_executor.py`）並解析 `.zip` 回測結果
- [ ] LLM 獨立模組透過 API 呼叫
- [ ] quant_alpha plugin 改名
- [ ] agent 自動 review 哪些需要歸納成 skills/rules/code
- [ ] `docs/USER_MANAGEMENT_PLAN_ZH.md` planka 多用戶模式
