## REMOVED Requirements

### Requirement: LangGraph interrupt() 暫停機制
**Reason**: interrupt() 依賴 LangGraph checkpoint 機制，移除 LangGraph 後無法使用。Planka column 移動本身已具備等效的暫停與恢復語意。
**Migration**: 需人工介入時，系統主動移卡至 `Review` column 並在 `projects.config` 記錄暫停原因。人工移回 `Executing` 時 webhook 觸發恢復。

### Requirement: Command(resume=decision) 恢復機制
**Reason**: 移除 LangGraph 後 Command 物件不存在。
**Migration**: 恢復邏輯改為：webhook 觸發 `Executing`，讀取 `workflow_step` 繼續下一步。決策（approve/reject）由 Planka comment 或 card 自定義欄位傳遞，step 函式讀取後繼續執行。

## MODIFIED Requirements

### Requirement: 人工介入（HITL）流程
系統 SHALL 透過 Planka column 移動實現人工介入。需人工審核時，系統 SHALL 移卡至 `Review` column，並在 Planka card 留 comment 說明原因。人工確認後將卡片移回 `Executing`，系統 SHALL 繼續執行 `workflow_step` 所指向的步驟。

#### Scenario: Plan 需人工審核
- **WHEN** `run_plan()` 判定需要人工確認（`needs_human_approval = True`）
- **THEN** 系統在 `projects.config` 記錄 `paused_at = "plan_approval"`，移卡至 `Review`，在 card comment 顯示計畫內容與審核指引

#### Scenario: 人工批准後恢復
- **WHEN** 人工將卡片從 `Review` 移至 `Executing`
- **THEN** webhook 觸發，系統讀取 `paused_at = "plan_approval"`，以「approved」決策繼續執行 `implement`

#### Scenario: 人工拒絕後重新計畫
- **WHEN** 人工在 card comment 留下拒絕原因後將卡片移至 `Executing`
- **THEN** 系統讀取最新 comment 作為 revise 指令，重新執行 `plan` step

#### Scenario: TERMINATE 後人工決定下一步
- **WHEN** 卡片移至 `Review`（TERMINATE 原因）
- **THEN** 系統不自動繼續；人工可移回 `Planning` 修改 spec，或直接歸檔
