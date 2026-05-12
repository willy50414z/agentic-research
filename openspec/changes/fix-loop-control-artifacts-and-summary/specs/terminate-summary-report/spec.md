## ADDED Requirements

### Requirement: TERMINATE step 觸發 LLM 跨輪綜合報告

當 `workflow_step = terminate` 時，系統 SHALL 執行 `terminate_summarize_step`，由 LLM 綜合所有輪的執行歷程（strategy params、metrics、修訂方向）與終止原因，產出跨輪綜合報告。

#### Scenario: TERMINATE handler 被呼叫
- **WHEN** `dispatch_step` 讀取 `workflow_step = terminate`
- **THEN** `_run_terminate_summarize` handler 執行，不再跳過（現有行為：handler 不存在，直接 return）

#### Scenario: LLM 生成報告
- **WHEN** `terminate_summarize_step` 呼叫 LLM 成功
- **THEN** 報告內容包含所有輪的策略參數、IS/OOS metrics、分析結論、以及下一步修改方向建議

#### Scenario: LLM 不可用時使用 fallback
- **WHEN** LLM call 失敗或超時
- **THEN** 系統 SHALL 使用 rule-based fallback 產出基本報告（現有 `terminate_summarize_step` fallback 邏輯），不拋出例外

---

### Requirement: 綜合報告以版本化命名上傳 Planka

`_run_terminate_summarize` handler SHALL 將報告以 `v{loop_index}_{max_loops}_summary_report.md` 命名，上傳至 Planka 卡片附件，並移卡至 Review。

命名範例：`max_loops=2` 且 `loop_index=0` 時，報告命名為 `v0_2_summary_report.md`。

#### Scenario: 報告上傳成功
- **WHEN** 報告產出成功，`upload_spec_attachment` 正常執行
- **THEN** Planka 卡片附件出現 `v0_2_summary_report.md`，卡片移至 Review

#### Scenario: 上傳失敗不中斷卡片移動
- **WHEN** `upload_spec_attachment` 拋出例外
- **THEN** 系統 SHALL log warning，卡片仍 SHALL 移至 Review；本地報告檔案保留

#### Scenario: workflow_step 更新為 done
- **WHEN** `_run_terminate_summarize` 完成（不論上傳是否成功）
- **THEN** `workflow_step` SHALL 設為 `done`，防止 dispatch loop 重複執行
