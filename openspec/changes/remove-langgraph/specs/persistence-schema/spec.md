## ADDED Requirements

### Requirement: DB schema 全部重建，不做 migration
系統 SHALL 以 drop + create 方式重建所有 DB tables。不保留舊資料，不執行 `ALTER TABLE` migration。重建後 schema 包含 `projects.workflow_step` 欄位。

#### Scenario: 全新環境啟動
- **WHEN** DB 為空（新環境或手動 drop all）
- **THEN** 啟動時系統自動建立所有 tables，包含 `workflow_step` 欄位

#### Scenario: 舊環境重建
- **WHEN** 手動執行 drop all scripts 後重啟
- **THEN** 系統以新 schema 重建，舊 LangGraph checkpoint tables 不再存在

### Requirement: projects 表包含 workflow_step 欄位
`projects` table SHALL 包含 `workflow_step VARCHAR(64) DEFAULT NULL` 欄位。有效值為：`plan`、`implement`、`test`、`analyze`、`summarize`、`spec_review_initial`、`spec_review_synthesize`、`done`、`terminated`。

#### Scenario: 新 project 首次執行
- **WHEN** project 第一次移入 `Executing` column
- **THEN** `workflow_step` 由 NULL 初始化為 `"plan"`

#### Scenario: Step 完成後更新
- **WHEN** 任意 step 函式成功完成
- **THEN** 系統立即執行 `UPDATE projects SET workflow_step = '<next>' WHERE id = '<project_id>'`（autocommit=True）

#### Scenario: Crash 後重新執行
- **WHEN** 進程重啟，webhook 再次觸發 `Executing`
- **THEN** 系統讀取 `workflow_step`，從上次已完成的 step 之後繼續執行

## REMOVED Requirements

### Requirement: LangGraph checkpoint tables
**Reason**: 移除 PostgresSaver 後，LangGraph 自動建立的 checkpoint tables（`checkpoints`、`checkpoint_blobs`、`checkpoint_migrations`）不再存在於新 schema。
**Migration**: 全部重建，不保留。
