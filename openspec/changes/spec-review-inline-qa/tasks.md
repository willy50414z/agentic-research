## 1. PlankaSink — 讀取 comment thread

- [x] 1.1 新增 `PlankaSink.get_card_comments(card_id: str) -> list[dict]`：呼叫 `GET /api/cards/{cardId}/actions`，過濾 `commentCard`，按 `createdAt` 升序回傳，失敗回 `[]`

## 2. server.py — 注入 comment thread

- [x] 2.1 在 `_run_spec_review_bg` 下載 spec.md 後，呼叫 `_planka_sink.get_card_comments(card_id)`
- [x] 2.2 將 `planka_comments` 和 `has_pending_qa: False` 加入 `initial_state`

## 3. spec_review_graph.py — State、init、routing

- [x] 3.1 `SpecReviewState` 新增 `planka_comments: list` 和 `has_pending_qa: bool`
- [x] 3.2 `_spec_review_init` 加入 Q&A 偵測：有問題 comment + 後續 comment → `has_pending_qa=True, total_rounds=1`
- [x] 3.3 新增 `_format_comment_history(comments) -> str` helper
- [x] 3.4 `_spec_review_round` 加入 refine 路由：`has_pending_qa` 時 `role="refine"`，傳入 `comment_history`

## 4. spec_clarifier.py + prompt

- [x] 4.1 `run_spec_agent` 加 `comment_history: str = ""` 參數，代入 `{COMMENT_HISTORY}` placeholder
- [x] 4.2 `_role_output_map` 加 `"refine": "reviewed_spec_final.md"`
- [x] 4.3 `_load_prompt` 加 refine fallback → synthesize
- [x] 4.4 新增 `framework/prompts/spec_agent_refine.txt`

## 5. 驗證

- [ ] 5.1 手動測試：上傳不完整 spec.md → 確認 Planka 出現問題 comment，card 移到 Planning，spec.md 未被修改
- [ ] 5.2 手動測試：在 Planka 回 comment → 移卡 → 確認 log 顯示 `has_pending_qa=True, role=refine`
- [ ] 5.3 手動測試：refine 通過 → card 移到 Verify
- [ ] 5.4 邊界測試：不回答直接移卡 → 確認走 initial review（`has_pending_qa=False`）
