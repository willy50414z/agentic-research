## Context

目前 spec review `need_update` 路徑把問題貼到 Planka comment，但 user 要在 comment 看問題、另開編輯器改 spec.md、再重新上傳附件才能回答。User 的 spec.md 編輯流程是 download → edit → upload，摩擦力與現在相同。

`framework/planka.py` 的 `PlankaSink` 已有 `post_comment` 和 `upload_spec_attachment`，但沒有讀取 comment 的方法。Planka API 的 `GET /api/cards/{id}/actions` 回傳含 `type=="commentCard"` 的 action 列表，可用 Bearer token 直接讀取。

## Goals / Non-Goals

**Goals:**
- User 只需在 Planka 回一則 comment 即可提供 Q&A 回答，不需碰 spec.md 檔案
- spec.md 永遠是乾淨的 spec 文件，不嵌入任何 Q&A 對話
- `need_update` 現有行為（post comment + 移 Planning）完全不變

**Non-Goals:**
- 不更改 `pass` 路徑
- 不引入新 Planka column
- 不限制最大迭代輪數

## Decisions

### 1. 偵測機制：系統問題 comment + user 回覆

```python
_QUESTION_MARKER = "**Spec 審查問題**"
question_indices = [i for i, c in enumerate(comments) if _QUESTION_MARKER in c["text"]]
has_pending_qa = bool(question_indices) and question_indices[-1] < len(comments) - 1
```

`has_pending_qa = True` 需同時滿足：
1. 有含 `_QUESTION_MARKER` 的 comment（系統發的）
2. 該 comment 之後還有至少一則 comment（user 的回答）

**Rationale：** 若 user 移卡但未回答（沒有後續 comment），`has_pending_qa=False`，走一般 initial review，不進入 refine 迴圈，避免空轉。

### 2. comment thread 格式化

`_format_comment_history(comments)` 產出：
```
=== Comment 1 (2026-03-30T12:00:00) ===
**Spec 審查問題**

- Q1: ...
- Q2: ...

=== Comment 2 (2026-03-30T12:05:00) ===
Q1 答案：...
Q2 答案：...
```

LLM 自行從 thread 中識別哪些是問題、哪些是回答，不需額外解析。

### 3. refine 是單輪，不走 multi-round review

`has_pending_qa=True` 時 `total_rounds=1`，直接執行 refine，不再跑 initial/review(s)/synthesize 流程。refine 後若仍有問題，再次 post comment，下次 re-trigger 再 refine。

### 4. prompt fallback：refine → synthesize

`spec_agent_refine.txt` 不存在時 fallback 到 `spec_agent_synthesize.txt`，兩者輸出規格相同（`reviewed_spec_final.md` + status file）。

## Risks / Trade-offs

**[Risk] User 未回答直接移卡** → `has_pending_qa=False`，走 initial review，LLM 重新審查並再次提問。這是預期行為。

**[Risk] comment thread 過長** → 傳給 LLM 的 context 增大。Mitigation：只傳問題 comment 之後的 comments（可日後優化），目前傳全部 thread，數量通常很少。

**[Risk] Planka `GET /actions` API 回傳格式不穩定** → `get_card_comments` 設計為 non-blocking（exception 回傳 `[]`），最壞情況退化為一般 initial review。
