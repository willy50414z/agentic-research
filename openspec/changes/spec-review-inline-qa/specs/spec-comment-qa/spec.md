## ADDED Requirements

### Requirement: 讀取卡片 comment thread
系統 SHALL 在 spec review 啟動時讀取對應 Planka 卡片的全部 comment。

- `PlankaSink.get_card_comments(card_id)` SHALL 呼叫 `GET /api/cards/{cardId}/actions`
- SHALL 過濾 `type == "commentCard"`，回傳 `[{"text": str, "createdAt": str}]`，按 `createdAt` 升序排列
- 失敗時 SHALL 回傳 `[]`，不拋出例外

#### Scenario: 正常取得 comments
- **WHEN** PlankaSink 呼叫 `get_card_comments` 且 API 回應正常
- **THEN** 回傳按時間升序排列的 comment 列表

#### Scenario: API 失敗時不中斷流程
- **WHEN** Planka API 回傳錯誤或逾時
- **THEN** 回傳空列表 `[]`，spec review 繼續以一般 initial review 路徑執行

### Requirement: Q&A 偵測邏輯
系統 SHALL 自動偵測 comment thread 中是否存在「系統問題 + user 回答」的組合。

- `_spec_review_init` SHALL 搜尋含 `"**Spec 審查問題**"` 的 comment
- 若最後一個問題 comment 之後還有其他 comment，SHALL 設定 `has_pending_qa = True`
- 否則 SHALL 設定 `has_pending_qa = False`

#### Scenario: 偵測到 Q&A（有問題也有回答）
- **WHEN** comment thread 含系統問題 comment，且其後有至少一則 comment
- **THEN** `has_pending_qa = True`，`total_rounds = 1`，走 refine 路徑

#### Scenario: 有問題但無回答
- **WHEN** comment thread 含系統問題 comment，但無後續 comment
- **THEN** `has_pending_qa = False`，走一般 initial review 路徑

#### Scenario: 無問題 comment
- **WHEN** comment thread 中無含 `"**Spec 審查問題**"` 的 comment
- **THEN** `has_pending_qa = False`，走一般 initial review 路徑
