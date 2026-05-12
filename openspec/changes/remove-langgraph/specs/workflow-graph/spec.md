## REMOVED Requirements

### Requirement: LangGraph StateGraph 作為工作流引擎
**Reason**: StateGraph 僅包裝 if/elif routing 與 Postgres 持久化，引入 7 個套件但無對應架構價值。以 step-based 純 Python 函式取代，達到相同效果並降低技術棧複雜度。
**Migration**: `framework/graph.py` 與 `framework/spec_review_graph.py` 刪除。工作流邏輯移至 `framework/workflow.py`（step dispatcher）。

### Requirement: PostgresSaver checkpointer 持久化 graph state
**Reason**: PostgresSaver 序列化完整 state 到 Postgres 的目的是 crash recovery，可由 `projects.workflow_step` 欄位以更低成本達到相同效果。
**Migration**: `projects` table 新增 `workflow_step varchar` 欄位。`projects.config`（JSONB）繼續儲存完整 state dict。

### Requirement: graph cache（get_or_build_graph）
**Reason**: 移除 StateGraph 後 graph cache 無用武之地。
**Migration**: 無需替代，step 函式為無狀態函式，可直接呼叫。

## MODIFIED Requirements

### Requirement: 工作流步驟執行順序
系統 SHALL 依以下順序執行研究工作流步驟：`plan → implement → test → analyze`，analyze 結果決定後續路由。PASS 繼續至 `summarize`，FAIL 回到 `implement`（受 max_loops 限制），TERMINATE 結束至 `Review`。

#### Scenario: 正常研究 loop
- **WHEN** 新 project 開始執行，`workflow_step` 初始化為 `"plan"`
- **THEN** 系統依序執行 plan → implement → test → analyze，每步完成後更新 `workflow_step`

#### Scenario: analyze PASS 路由
- **WHEN** `run_analyze()` 回傳 `last_result = "PASS"`
- **THEN** 系統執行 `run_summarize()`，完成後移卡至 `Done`，`workflow_step = "done"`

#### Scenario: analyze FAIL 路由（未超上限）
- **WHEN** `run_analyze()` 回傳 `last_result = "FAIL"`，`attempt_index < max_loops`
- **THEN** `workflow_step` 設為 `"implement"`，系統繼續執行下一次 implement
