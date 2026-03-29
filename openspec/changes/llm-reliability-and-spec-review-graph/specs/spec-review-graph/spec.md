## ADDED Requirements

### Requirement: Spec review as LangGraph StateGraph
系統 SHALL 以獨立的 LangGraph StateGraph（`spec_review_graph`）執行 spec review 工作流，取代原有的純 Python 背景任務 `_run_spec_review_bg`。graph SHALL 使用 PostgresSaver 作為 checkpointer，以 `project_id` 作為 `thread_id`。

#### Scenario: Spec review graph invoked on card move
- **WHEN** Planka 卡片移動到 "Spec Pending Review" 觸發 webhook
- **THEN** server 以 `project_id` 為 thread_id 呼叫 `spec_review_graph.invoke(initial_state)`

#### Scenario: Checkpoint saved after each round
- **WHEN** `spec_review_round` 節點執行完成一輪
- **THEN** PostgresSaver 將目前 state（含 `current_round`、`review_notes`、`current_spec_md`）寫入 checkpoint

### Requirement: Dynamic round count from LLM_CHAIN
`total_rounds` SHALL 等於 `len(participants) + 1`，其中 `participants` 為 `LLM_CHAIN` 拆分後的有序清單。round 編號從 0 開始。

#### Scenario: Two-provider chain
- **WHEN** `LLM_CHAIN = "claude-cli,gemini-cli"`
- **THEN** `total_rounds = 3`（round 0: claude author, round 1: gemini reviewer, round 2: claude synthesizer）

#### Scenario: Three-provider chain
- **WHEN** `LLM_CHAIN = "claude-cli,gemini-cli,codex-cli"`
- **THEN** `total_rounds = 4`（round 0: claude author, round 1: gemini reviewer, round 2: codex reviewer, round 3: claude synthesizer）

#### Scenario: Single-provider chain
- **WHEN** `LLM_CHAIN = "claude-cli"`
- **THEN** `total_rounds = 2`（round 0: author, round 1: synthesizer，均由 claude 執行）

### Requirement: Author/Reviewer/Synthesizer role assignment
`spec_review_round` 節點 SHALL 根據 `current_round` 決定角色與呼叫的 LLM：

- `current_round == 0`：author，呼叫 `participants[0]`，使用 `initial` prompt role
- `0 < current_round < total_rounds - 1`：reviewer，呼叫 `participants[current_round]`，使用 `review` prompt role
- `current_round == total_rounds - 1`：synthesizer，呼叫 `participants[0]`，使用 `synthesize` prompt role

Author 與 Synthesizer SHALL 更新 `current_spec_md`。Reviewer SHALL 只往 `review_notes` 追加意見，不修改 `current_spec_md`。

#### Scenario: Author updates spec
- **WHEN** round 0 執行完成，LLM 產出 `reviewed_spec_initial.md`
- **THEN** state 的 `current_spec_md` 更新為此檔案內容，`current_round` 遞增至 1

#### Scenario: Reviewer appends notes only
- **WHEN** round 1（reviewer 角色）執行完成
- **THEN** `review_notes` 新增一筆 `{"participant": "gemini-cli", "round": 1, "questions": [...]}`，`current_spec_md` 不變

#### Scenario: Synthesizer writes final spec
- **WHEN** 最終輪（synthesizer 角色）執行完成，LLM 產出 `reviewed_spec_final.md` 與 `status_pass.txt`
- **THEN** `current_spec_md` 更新，`status` 設為 `"pass"`，`spec_review_round` 將路由至 `spec_finalize`

### Requirement: Error resume from last completed round
若 `spec_review_round` 節點因 LLM 呼叫失敗而拋出例外，LangGraph SHALL 保留上一個成功 checkpoint 的 state。重新觸發後（例如使用者重新移卡片），server SHALL 以相同 `thread_id` resume，從失敗輪次重新執行，已完成輪次不重跑。

#### Scenario: Resume after reviewer failure
- **WHEN** round 2（codex reviewer）呼叫失敗，round 0 與 round 1 已 checkpoint
- **THEN** resume 後 `current_round` 從 2 開始，round 0（claude author）與 round 1（gemini reviewer）的結果完整保留

### Requirement: Spec finalize node
`spec_finalize` 節點 SHALL 讀取 state 中的 `status` 與 `questions`，執行以下操作：
- 若 `status == "pass"`：解析最終 spec，呼叫 `create_project`，將 Planka 卡片移至 "Verify"，啟動 research graph
- 若 `status == "need_update"`：將 `questions` 作為 comment 貼到 Planka 卡片，移卡片至 "Planning"
- 若 `status == "abort"`：將錯誤訊息貼到 Planka 卡片，移卡片至 "Planning"

#### Scenario: Finalize on pass
- **WHEN** `spec_finalize` 執行且 `status == "pass"`
- **THEN** `create_project` 被呼叫，卡片移至 "Verify"，research graph 被觸發

#### Scenario: Finalize on need_update
- **WHEN** `spec_finalize` 執行且 `status == "need_update"`
- **THEN** questions 以 Planka comment 形式發布，卡片移至 "Planning"

### Requirement: Spec review prompt roles
`spec_clarifier.run_spec_agent()` SHALL 支援 `role` 參數值 `"initial"`、`"review"`、`"synthesize"`，各對應獨立的 prompt 模板檔案。

- `initial`（`spec_agent_initial.txt`）：讀 `spec.md`，產出 `reviewed_spec_initial.md` + `status_pass.txt` 或 `status_need_update.txt`
- `review`（`spec_agent_review.txt`）：讀 `current_spec_md`，產出 `review_notes_round{N}.txt`，不產出 reviewed spec 檔案
- `synthesize`（`spec_agent_synthesize.txt`）：讀 `current_spec_md` + 所有 `review_notes_round*.txt`，產出 `reviewed_spec_final.md` + `status_pass.txt` 或 `status_need_update.txt`

#### Scenario: review role produces notes file only
- **WHEN** `run_spec_agent(role="review", round_index=1)` 執行完成
- **THEN** `work_dir` 中存在 `review_notes_round1.txt`，不存在 `reviewed_spec_*.md`

#### Scenario: synthesize role reads all review notes
- **WHEN** `run_spec_agent(role="synthesize")` 執行
- **THEN** prompt 包含 `work_dir` 中所有 `review_notes_round*.txt` 的內容，LLM 產出整合所有意見的最終 spec
