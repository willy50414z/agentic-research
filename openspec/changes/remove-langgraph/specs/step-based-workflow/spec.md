## ADDED Requirements

### Requirement: Step dispatcher 依 workflow_step 執行對應步驟
系統 SHALL 在收到 Planka `Executing` column 的 webhook 時，從 DB 讀取 `projects.workflow_step`，並執行對應的步驟函式。步驟函式完成後必須更新 `workflow_step` 為下一步名稱，或將卡片移至終態 column。

#### Scenario: 正常步驟串接
- **WHEN** `Executing` webhook 觸發，`workflow_step = "plan"`
- **THEN** 系統執行 `run_plan()`，完成後寫 `workflow_step = "implement"`，卡片保持在 `Executing`

#### Scenario: 步驟到達終態 PASS
- **WHEN** `run_analyze()` 判定 `last_result = "PASS"`
- **THEN** 系統寫 `workflow_step = "summarize"`，繼續執行 `run_summarize()`，完成後移卡至 `Done`

#### Scenario: 步驟到達終態 TERMINATE
- **WHEN** `run_analyze()` 判定 `last_result = "TERMINATE"`
- **THEN** 系統移卡至 `Review`，在 Planka comment 記錄 TERMINATE 原因

#### Scenario: FAIL 回到 implement 繼續 retry
- **WHEN** `run_analyze()` 判定 `last_result = "FAIL"` 且 `attempt_index < max_loops`
- **THEN** 系統寫 `workflow_step = "implement"`，在同一 `Executing` column 繼續下一次 retry

#### Scenario: FAIL 超出 max_loops
- **WHEN** `run_analyze()` 判定 `last_result = "FAIL"` 且 `attempt_index >= max_loops`
- **THEN** 系統將 `last_result` 覆寫為 `"TERMINATE"`，後續行為同 TERMINATE 分支

### Requirement: 每個步驟函式為冪等原子操作
每個 step 函式（run_plan / run_implement / run_test / run_analyze / run_summarize）SHALL 設計為冪等：重複執行同一 step 不得造成資料損壞或不可恢復的副作用。

#### Scenario: Crash 後重跑同一步驟
- **WHEN** 步驟函式執行中途進程崩潰，`workflow_step` 未更新至下一步
- **THEN** 重啟後 webhook 重新觸發，系統再次執行同一 step，結果覆蓋前次不完整輸出

### Requirement: Plugin node 簽名維持不變
步驟函式 SHALL 呼叫 `ResearchPlugin` 的 node 方法（`plan_node`, `implement_node`, `test_node`, `analyze_node`, `summarize_node`），簽名為 `(state: dict) -> dict`，與 LangGraph 時期完全相同。

#### Scenario: 現有 plugin 不需修改
- **WHEN** 框架執行 step-based workflow
- **THEN** plugin 的 node 方法以 `state dict` 呼叫並接收 `partial state dict` 回傳，plugin 無需感知底層執行引擎

### Requirement: Spec 審查工作流改為 step-based
Spec 審查 SHALL 以 `workflow_step`（`spec_review_initial` / `spec_review_synthesize`）控制兩個審查 round，不使用獨立的 StateGraph。

#### Scenario: Spec 審查初始 round
- **WHEN** `Spec Pending Review` webhook 觸發，`workflow_step = NULL` 或 `"spec_review_initial"`
- **THEN** 系統執行 initial LLM round，完成後寫 `workflow_step = "spec_review_synthesize"`

#### Scenario: Spec 審查 synthesize round
- **WHEN** `Spec Pending Review` webhook 觸發，`workflow_step = "spec_review_synthesize"`
- **THEN** 系統執行 synthesize LLM round，結果決定移卡至 `Executing` 或 `Planning`
