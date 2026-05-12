## ADDED Requirements

### Requirement: 錯誤時自動寫出 last_error.txt
當 `dispatch_step` 或 `spec_review_step` 捕捉到未預期例外時，系統 SHALL 在 `artifacts/errors/last_error.txt` 寫出結構化錯誤報告，覆寫任何既有內容。

#### Scenario: dispatch_step 失敗時寫出報告
- **WHEN** `dispatch_step` 的 except 區塊捕捉到例外
- **THEN** 系統在呼叫現有 logger + Planka comment 後，寫出 `last_error.txt`

#### Scenario: spec_review_step 失敗時寫出報告
- **WHEN** `spec_review_step.run()` 的任一 except 區塊捕捉到例外
- **THEN** 系統寫出 `last_error.txt`，無論 Planka client 是否可用

#### Scenario: artifacts/errors 目錄不存在時自動建立
- **WHEN** `artifacts/errors/` 目錄尚未建立
- **THEN** 系統 SHALL 自動建立該目錄，不拋出例外

### Requirement: 報告內容涵蓋 Claude 診斷所需的完整 context
`last_error.txt` SHALL 包含以下區塊：header（timestamp、project_id、card_id、Planka URL）、狀態（workflow_step、last_result、last_reason、review_in_progress）、spec 摘要（各頂層 key 是否存在）、環境變數（BACKTEST_MODE、LLM 相關 env）、完整 Python traceback。

#### Scenario: card_id 存於 DB 時包含於報告
- **WHEN** `projects.config.planka_card_id` 存在
- **THEN** 報告的 header 區塊包含 card_id 及 `{PLANKA_API_URL}/cards/{card_id}` 格式的 URL

#### Scenario: DB 不可用時報告仍寫出
- **WHEN** DB 查詢失敗（連線錯誤等）
- **THEN** 無法取得的欄位標記為 `(unavailable)`，其餘已知欄位照常寫出，不拋出例外

### Requirement: 寫出報告失敗不影響原有錯誤處理
`write_error_report()` SHALL 在自身發生任何例外時，僅記錄 warning 並靜默返回，不重新拋出例外。

#### Scenario: 磁碟寫入失敗
- **WHEN** `artifacts/errors/last_error.txt` 寫入因磁碟錯誤失敗
- **THEN** 系統記錄 `logger.warning`，原有的 Planka comment 及卡片移動流程不受影響
