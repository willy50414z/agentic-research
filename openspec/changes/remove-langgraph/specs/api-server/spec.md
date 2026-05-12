## MODIFIED Requirements

### Requirement: Planka webhook 路由支援 Executing column
`POST /planka-webhook` SHALL 處理 `Executing` column 的卡片移入事件，取代原有 `Verify` column handler。收到事件後讀取 `projects.workflow_step`，dispatch 至對應的 step 函式。

#### Scenario: 卡片移入 Executing，有 workflow_step
- **WHEN** webhook 事件 `listName = "Executing"`，DB 有對應 project 且 `workflow_step` 不為 NULL
- **THEN** 系統在背景執行 `workflow_step` 對應的 step 函式，回應 `{"status": "accepted"}`

#### Scenario: 卡片移入 Executing，無 workflow_step（首次啟動）
- **WHEN** webhook 事件 `listName = "Executing"`，`workflow_step` 為 NULL
- **THEN** 系統將 `workflow_step` 初始化為 `"plan"`，執行 `run_plan()`

#### Scenario: 重複 webhook（同一 step 已在執行）
- **WHEN** webhook 觸發時同一 project 已有背景任務在執行
- **THEN** 系統忽略本次 webhook，回應 `{"status": "skipped", "reason": "already_running"}`

## REMOVED Requirements

### Requirement: Verify column webhook handler
**Reason**: `Verify` column 改名為 `Executing`，handler 隨之更新。
**Migration**: 將 `_COL_VERIFY = "Verify"` 改為 `_COL_EXECUTING = "Executing"`。Planka board 同步改名。

### Requirement: /resume endpoint 與 Command(resume=...) 邏輯
**Reason**: HITL 恢復改由 Planka column 移動觸發，不再需要獨立的 /resume HTTP endpoint。
**Migration**: 移除 `POST /resume` endpoint 與 `_run_resume_bg`。恢復邏輯整合至 `Executing` column handler。
