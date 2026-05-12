## ADDED Requirements

### Requirement: max_loops 從 Planka 卡片同步至 DB

當卡片移入 Executing 欄時，系統 SHALL 從該卡片的 `max_loops` custom field 讀取數值，
並以 `merge_config` 寫入 DB 的 `projects.config`，確保後續 `dispatch_step` 使用正確上限。

若 custom field 不存在、為空、或無法解析為整數，系統 SHALL 不寫入 DB（保留 DB 既有值或 fallback 預設值 3）。

#### Scenario: 卡片有有效的 max_loops 值
- **WHEN** Planka 卡片移入 Executing，且 `max_loops` custom field 值為有效正整數（例如 `2`）
- **THEN** `projects.config.max_loops` 被設為該整數，`dispatch_step` 以此值作為迴圈上限

#### Scenario: 卡片未設定 max_loops
- **WHEN** Planka 卡片移入 Executing，且 `max_loops` custom field 為空或不存在
- **THEN** DB 不寫入任何值，`_build_state` fallback 至預設值 3

#### Scenario: max_loops 為非整數字串
- **WHEN** Planka 卡片的 `max_loops` custom field 值為非整數字串（例如 `"abc"`）
- **THEN** 系統 MUST 捕捉例外，不寫入 DB，並 log warning；workflow 繼續執行

#### Scenario: max_loops 正確限制迴圈次數
- **WHEN** `max_loops` 設為 2，且兩輪 FAIL 後 `analyze_attempt` 達到 2
- **THEN** 系統 SHALL 判定為 TERMINATE，不再進入第 3 輪
