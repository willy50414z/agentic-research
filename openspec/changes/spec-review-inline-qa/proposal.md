## Why

當 LLM spec review 發現問題時，目前做法是把問題貼到 Planka comment，但 user 必須在 comment 讀問題、另外開編輯器改 spec.md、再重新上傳，流程破碎。改為讓 user 直接在 Planka 回一則 comment 作為回答，re-trigger 時系統讀 comment thread 傳給 LLM 做 refine。spec.md 永遠保持乾淨的 spec 內容，Q&A 歷史留在 Planka comment 裡。

## What Changes

- **新增 `PlankaSink.get_card_comments`**：讀取卡片全部 comment，按時間排序回傳
- **server.py 注入 comment thread**：`_run_spec_review_bg` 在下載 spec.md 後，同步抓 comment thread 並注入 `initial_state`
- **Q&A 偵測**：`_spec_review_init` 偵測是否有「系統問題 comment + user 回覆」，有則設 `has_pending_qa=True`、`total_rounds=1`
- **新增 `refine` role**：`_spec_review_round` 在 `has_pending_qa` 時走 refine 路徑；`run_spec_agent` 接收 `comment_history` 並代入 prompt
- **新增 `spec_agent_refine.txt`**：prompt 指示 LLM 讀 comment thread，整合 user 回答精煉 spec
- **`need_update` 路徑不變**：繼續 post comment 貼問題，card 移 Planning（現有行為）

## Capabilities

### New Capabilities

- `spec-comment-qa`: 以 Planka comment thread 承載 Q&A，系統偵測 user 回答後自動路由至 refine

### Modified Capabilities

- `spec-review-graph`: 新增 `planka_comments` / `has_pending_qa` State 欄位；新增 refine 路由邏輯

## Impact

- `framework/planka.py`：新增 `get_card_comments(card_id)` 方法
- `framework/api/server.py`：`_run_spec_review_bg` 注入 `planka_comments` / `has_pending_qa` 至 initial_state
- `framework/spec_review_graph.py`：State 新欄位、init 偵測邏輯、`_format_comment_history` helper、round refine 路由
- `framework/spec_clarifier.py`：`run_spec_agent` 加 `comment_history` 參數；`refine` role output map；prompt fallback
- `framework/prompts/spec_agent_refine.txt`：新 prompt 檔案
