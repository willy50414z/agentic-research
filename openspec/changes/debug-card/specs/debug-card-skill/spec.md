## ADDED Requirements

### Requirement: /debug-card skill 無參數時讀取 last_error.txt
當使用者輸入 `/debug-card`（無 card_id），skill SHALL 讀取 `artifacts/errors/last_error.txt`，從中解析 project_id 與 card_id，並查詢 DB 取得最新狀態後呈現給 Claude。

#### Scenario: last_error.txt 存在時自動取得 context
- **WHEN** 使用者輸入 `/debug-card` 且 `artifacts/errors/last_error.txt` 存在
- **THEN** skill 讀取該檔案，解析 project_id，查詢 DB 取得最新 config，並將兩者合併呈現

#### Scenario: last_error.txt 不存在時提示使用者
- **WHEN** 使用者輸入 `/debug-card` 且 `artifacts/errors/last_error.txt` 不存在
- **THEN** skill 告知使用者找不到錯誤報告，並說明觸發錯誤後會自動產生該檔案

### Requirement: /debug-card {card_id} 依 card_id 查詢
當使用者提供 card_id 參數，skill SHALL 透過 DB 的 `planka_card_id` 欄位反查 project_id，再取得完整 project 狀態。

#### Scenario: 以 card_id 找到對應 project
- **WHEN** 使用者輸入 `/debug-card 1767580988747023379`
- **THEN** skill 查詢 DB 找到 project_id，取得完整 config 並呈現診斷資訊

#### Scenario: card_id 找不到對應 project
- **WHEN** 提供的 card_id 在 DB 中無對應記錄
- **THEN** skill 告知使用者找不到該 card_id 的 project，並建議確認 card_id 是否正確

### Requirement: 診斷呈現包含可供 Claude 直接分析的結構化資訊
Skill 呈現的診斷資訊 SHALL 包含：project_id、card_id、Planka URL、workflow_step、last_result、last_reason、spec 完整內容、review_in_progress 狀態，以及 `last_error.txt` 的原始內容（若存在）。

#### Scenario: 完整診斷呈現
- **WHEN** skill 成功取得 project 資訊
- **THEN** 輸出包含上述所有欄位，讓 Claude 無需再詢問即可開始分析根本原因
