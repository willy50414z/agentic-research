## 1. loop-control-sync：max_loops 從 Planka 同步至 DB

- [x] 1.1 `app/api/server.py`：`run_dispatch_bg` 新增 `card_id: str` 參數
- [x] 1.2 `app/api/server.py`：Executing webhook handler 在 `background_tasks.add_task` 時傳入 `card_id`
- [x] 1.3 `app/api/server.py`：`run_dispatch_bg` 呼叫 `planka_client.read_card_custom_fields(card_id)`，取得 `max_loops`
- [x] 1.4 `app/api/server.py`：將有效的 `max_loops` 以 `merge_config(project_id, {"max_loops": N})` 寫入 DB（含 try/except，無效值不寫入）

## 2. WorkflowStep.REVISE：新增枚舉與路由

- [x] 2.1 `app/workflow/constants.py`：新增 `REVISE = "revise"` 至 `WorkflowStep` enum
- [x] 2.2 `app/workflow/executing_step.py`：`_ANALYZE_NEXT_STEP[AnalysisResult.FAIL]` 改為 `WorkflowStep.REVISE`

## 3. fail-path-revise-plan：2-LLM REVISE step 實作

- [x] 3.1 `app/prompts/freqtrade/revise_validate.txt`：新增 LLM 2 驗證 prompt，輸入為 `revise_draft.json` 內容，輸出為最終 `revised_params.json`
- [x] 3.2 `app/freqtrade/steps.py`：`revise_step` 改為 2-LLM 串接：LLM 1 產出 `revise_draft.json`，LLM 2 產出 `revised_params.json`；各自含獨立 fallback
- [x] 3.3 `app/freqtrade/steps.py`：`revise_step` 產出 `v{N}_revised_direction.md`（N = `analyze_attempt`），內容含修訂原因與參數對照
- [x] 3.4 `app/workflow/executing_step.py`：新增 `_run_revise` handler，呼叫 `revise_step`，`merge_config` 更新 `implementation_plan`，上傳 `v{N}_revised_direction.md` 至 Planka，設定 `workflow_step = implement`
- [x] 3.5 `app/workflow/executing_step.py`：`_STEP_HANDLERS` 加入 `WorkflowStep.REVISE: _run_revise`

## 4. per-iteration-artifact-packaging：v{N} 命名與 zip 打包

- [x] 4.1 `app/freqtrade/steps.py`：`_mock_implement_result` artifact 命名由 `loop_{loop}_*` 改為 `v{N}_*`（N 從 state 讀取 `analyze_attempt`）
- [x] 4.2 `app/freqtrade/steps.py`：`_real_implement` artifact 命名同步改為 `v{N}_*`
- [x] 4.3 `app/workflow/executing_step.py`：`_run_analyze` 在 result 判定後，將本輪新增 artifact 打包為 `v{N}_backtest.zip`
- [x] 4.4 `app/workflow/executing_step.py`：以 `sink.upload_bytes_attachment` 上傳 zip，取代逐一上傳個別 artifact（含 try/except，失敗僅 log warning）

## 5. spec review 附件有序上傳

- [x] 5.1 `app/workflow/spec_review_step.py`：`_upload_work_dir` 新增 `_upload_priority` 排序函式（`reviewed_spec_initial.md` → 0，`reviewed_spec_final.md` → 1，其餘 → 2，同級按檔名排序）
- [x] 5.2 `app/workflow/spec_review_step.py`：`_upload_work_dir` 改用 `sorted(..., key=_upload_priority)` 取代 `iterdir()`

## 6. terminate-summary-report：TERMINATE handler 與跨輪報告

- [x] 6.1 `app/workflow/executing_step.py`：新增 `_run_terminate_summarize` handler，呼叫 `terminate_summarize_step(state)`
- [x] 6.2 `app/workflow/executing_step.py`：`_run_terminate_summarize` 將報告上傳為 `v{loop_index}_{max_loops}_summary_report.md`（含 try/except）
- [x] 6.3 `app/workflow/executing_step.py`：`_run_terminate_summarize` 移卡至 `PlankaColumn.REVIEW`，並設定 `workflow_step = done`
- [x] 6.4 `app/workflow/executing_step.py`：`_STEP_HANDLERS` 加入 `WorkflowStep.TERMINATE: _run_terminate_summarize`
