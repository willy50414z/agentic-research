## MODIFIED Requirements

### Requirement: SpecReviewState 包含 comment thread 欄位
`SpecReviewState` SHALL 包含 Planka comment 相關欄位。

- `planka_comments: list` — 原始 comment thread，來自 `get_card_comments`
- `has_pending_qa: bool` — 由 `_spec_review_init` 根據 comment thread 決定

#### Scenario: initial_state 注入 comments
- **WHEN** `_run_spec_review_bg` 建立 `initial_state`
- **THEN** `planka_comments` SHALL 包含從 Planka 讀取的 comment 列表
- **THEN** `has_pending_qa` SHALL 初始化為 `False`（由 init node 更新）

## ADDED Requirements

### Requirement: refine 路徑路由
當 `has_pending_qa == True` 時，spec review graph SHALL 執行單輪 refine，不走 multi-round review。

- `_spec_review_round` 在 `has_pending_qa == True` 且 `current_round == 0` 時 SHALL 以 `role = "refine"` 執行
- refine 時 SHALL 格式化 comment thread 傳給 `run_spec_agent` 作為 `comment_history`

#### Scenario: has_pending_qa 觸發 refine
- **WHEN** `has_pending_qa == True`
- **THEN** graph SHALL 執行單輪 refine，不執行 initial / review(s) / synthesize

#### Scenario: has_pending_qa=False 走原有流程
- **WHEN** `has_pending_qa == False`
- **THEN** graph SHALL 執行原有 initial → review(s) → synthesize 完整流程

### Requirement: refine role 產出
`refine` role 的 LLM SHALL 根據 comment thread 中的 user 回答精煉 spec。

- `refine` 通過時 SHALL 輸出 `reviewed_spec_final.md`（不含 Q&A 對話內容）及 `status_pass.txt`
- `refine` 仍有問題時 SHALL 輸出 `reviewed_spec_final.md` 及 `status_need_update.txt`
- `_spec_finalize` 的 `need_update` 路徑 SHALL 繼續 post comment 貼問題（現有行為不變）

#### Scenario: refine 通過
- **WHEN** user 回答足夠，LLM 能精煉出完整 spec
- **THEN** work_dir SHALL 包含 `reviewed_spec_final.md` 及 `status_pass.txt`
- **THEN** card SHALL 移至 Verify

#### Scenario: refine 仍有問題
- **WHEN** user 回答不足，LLM 仍有疑問
- **THEN** 系統 SHALL post 新問題 comment，card 移回 Planning
