## ADDED Requirements

### Requirement: Preflight check in server lifespan
FastAPI server 的 `_lifespan` SHALL 在啟動階段（yield 之前）同步呼叫 `preflight_check()`。若 `preflight_check()` 拋出例外，server SHALL 終止啟動流程。

#### Scenario: Server starts successfully after preflight
- **WHEN** `preflight_check()` 完成且所有服務驗證通過
- **THEN** server 繼續啟動，`app.state.preflight` 被設定，scheduler 正常啟動

#### Scenario: Server refuses to start on preflight failure
- **WHEN** `preflight_check()` 拋出 `RuntimeError`
- **THEN** FastAPI lifespan 中例外向上傳播，server process 退出，不接受任何請求

### Requirement: GET /health/llm endpoint
server SHALL 提供 `GET /health/llm` endpoint，回傳最近一次 preflight check 的結果。

#### Scenario: Returns preflight results as JSON
- **WHEN** `GET /health/llm` 被呼叫且 server 已完成 preflight
- **THEN** 回傳 HTTP 200，body 為 `{"ok": true, "validated_at": "...", "results": {...}}`

## MODIFIED Requirements

### Requirement: Spec Pending Review webhook handler
`_run_spec_review_bg` 函式 SHALL 被替換為呼叫 `spec_review_graph`。webhook handler 在接收到 "Spec Pending Review" 事件後，SHALL 在 background task 中以 `project_id` 作為 `thread_id` invoke spec_review_graph，而非直接執行 Python 函式。

#### Scenario: Webhook triggers spec review graph
- **WHEN** Planka webhook 事件的 column 為 "Spec Pending Review"
- **THEN** background task 呼叫 `spec_review_graph.invoke(initial_state, config={"configurable": {"thread_id": project_id}})`

#### Scenario: Idempotency check still applies
- **WHEN** 同一 project_id 的 spec review 已在進行中（`review_in_progress` flag 為 true 且未超時）
- **THEN** 新觸發被忽略，行為與原有邏輯一致
