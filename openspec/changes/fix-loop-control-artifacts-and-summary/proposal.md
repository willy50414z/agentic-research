## Why

agentic-research pipeline 在 Planka 卡片設定 `max_loops=2` 時實際跑了 3 輪（max_loops 從未從卡片讀入 DB）；FAIL 路徑沿用相同的 plan 重跑而未重新規劃；artifact 命名全為 `loop_0` 前綴導致後輪蓋掉前輪；spec review 附件上傳順序不確定；TERMINATE 後缺少跨輪綜合報告。這些問題共同造成研究迴圈難以追蹤、FAIL 路徑無實質改進、結果難以審閱。

## What Changes

- **Planka custom field → DB 寫入 max_loops**：Executing webhook handler 在 dispatch 前讀取卡片 `max_loops` custom field 並 `merge_config` 寫入 DB；`_build_state` 從 DB 讀取，確保上限正確
- **FAIL 路徑新增 REVISE step（2-LLM）**：analyze FAIL 後進入新的 `REVISE` step：LLM 1 根據 loop summary + metrics + 失敗原因產出修訂草稿，LLM 2 驗證並補充，最終產出 `v{N}_revised_direction.md` 上傳 Planka 並更新 `implementation_plan`，再進入 IMPLEMENT
- **每輪 artifact 打包為 `v{N}_backtest.zip`**：每輪（implement → test → analyze）產出的所有 artifact 以 `analyze_attempt` 計數命名（`v0_backtest.zip`、`v1_backtest.zip`…），打包後統一上傳，取代逐一上傳
- **spec review 附件有序上傳**：`reviewed_spec_initial.md` 保證先於 `reviewed_spec_final.md` 上傳
- **TERMINATE 觸發 LLM 綜合報告**：新增 TERMINATE step handler，呼叫 LLM 綜合所有輪執行歷程與修正軌跡，產出 `v{loop_index}_{max_loops}_summary_report.md` 上傳 Planka 並移卡至 Review

## Capabilities

### New Capabilities

- `loop-control-sync`：從 Planka 卡片 custom field 同步 `max_loops` 至 DB config，確保每次 dispatch 使用正確上限
- `fail-path-revise-plan`：FAIL 後以 2-LLM 流程（draft → validate）產出修訂方向，上傳 `v{N}_revised_direction.md` 至 Planka，再以新計畫進入下一輪 IMPLEMENT
- `per-iteration-artifact-packaging`：每輪 artifact 依 `analyze_attempt` 命名並打包為 `v{N}_backtest.zip` 上傳
- `terminate-summary-report`：TERMINATE 後由 LLM 生成跨輪綜合報告（`v{loop_index}_{max_loops}_summary_report.md`）並上傳 Planka

### Modified Capabilities

（無 spec-level 行為變更，僅 implementation 層修正）

## Impact

| 檔案 | 異動 |
|------|------|
| `app/api/server.py` | `run_dispatch_bg` 多傳 `card_id`；dispatch 前讀 custom fields 寫 `max_loops` |
| `app/workflow/constants.py` | 新增 `WorkflowStep.REVISE` |
| `app/workflow/executing_step.py` | `_ANALYZE_NEXT_STEP[FAIL]` 改指向 `REVISE`；新增 `_run_revise` / `_run_terminate_summarize` handler；zip 打包邏輯 |
| `app/freqtrade/steps.py` | `revise_step` 改為 2-LLM；artifact 路徑改用 `analyze_attempt` |
| `app/prompts/freqtrade/revise_validate.txt` | 新增（LLM 2 驗證用 prompt） |
| `app/workflow/spec_review_step.py` | `_upload_work_dir` 改為有序上傳（initial → final → 其他） |
| `app/clients/task_board.py` | `WorkflowSink` protocol 確認有 `upload_bytes_attachment` |

無 API schema breaking change；DB schema 不需修改。
