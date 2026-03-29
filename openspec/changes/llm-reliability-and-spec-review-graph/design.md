## Context

目前 `framework/llm_agent/llm_svc.py` 是 LLM 呼叫的唯一進入點，但它同時承擔了 transport（subprocess 封裝）、業務邏輯（Gemini prompt prefix）、工具輔助（Codex workspace trust）三種職責，且 `ping()` 函式有兩個明確 bug 從未被正確使用過。

Spec review 流程（`_run_spec_review_bg`）是純 Python 背景 thread，沒有任何 persistence。若 primary LLM 成功但 secondary 失敗，整個工作流重頭來，浪費 quota 且無法自動 resume。此外，目前硬寫 `llm_chain[0]`（primary）與 `llm_chain[1]`（secondary）兩個固定角色，鏈長超過 2 時多餘的 LLM 完全被忽略。

系統啟動時不做任何連通測試，等到 Planka 卡片觸發流程時才在 `_run_spec_review_bg` 內呼叫 `_build_llm_chain()`，此時若 LLM 沒登入或 Planka token 過期，流程中途爆炸，錯誤訊息留在 log 而非 Planka 卡片上。

## Goals / Non-Goals

**Goals:**
- `llm_svc.py` 只保留 transport 職責，移除所有業務邏輯與有 bug 的 `ping()`
- 系統啟動時執行 preflight check（LLM 連通性、Planka JWT、DB），失敗即阻止 server 啟動
- Spec review 工作流遷移至 LangGraph StateGraph，取得 PostgresSaver checkpoint 與 error resume 能力
- Spec review 輪數由 `LLM_CHAIN` 長度動態決定，採 Author → Reviewers → Synthesizer 模式

**Non-Goals:**
- LLM quota / usage 監控（待後續獨立研究）
- 有爭議 spec 段落轉為子研究計畫
- 新增 `LLMTarget` enum 成員或新 provider 支援

## Decisions

### D1：llm_svc.py — 移除 ping()，移出 Gemini prefix

`ping()` 的邏輯移入 `llm_preflight.py`，以正確的實作替代（每個 provider 獨立測試方法）。Gemini 的 `STRICT RULE` prompt prefix 是 spec review 的業務規則，屬於 `spec_clarifier.py` 的職責，移回呼叫端。

**替代考量**：在 `ping()` 原地修 bug → 不選，因為 preflight 需要集中管理所有 provider，分散在 `llm_svc.py` 裡的單一函式無法滿足需求。

### D2：Preflight — cache 策略

Preflight 結果寫入 `{VOLUME_BASE_DIR}/preflight_cache.json`，內容包含：
```json
{
  "chain_hash": "<sha256 of LLM_CHAIN env value>",
  "validated_at": "2026-03-28T10:00:00Z",
  "results": {
    "claude-cli": {"ok": true},
    "gemini-cli": {"ok": false, "reason": "not logged in"},
    "planka":     {"ok": true},
    "database":   {"ok": true}
  }
}
```

啟動時若 `chain_hash` 與現有 cache 相符且 `validated_at` 在 1 小時內，跳過重驗。否則重新執行全部驗證。

**替代考量**：每次啟動都重驗 → 不選，因為 container restart 頻繁時 `claude auth status` 需 1-2 秒，累積延遲明顯。永不重驗 → 不選，chain 新增成員時必須重驗。

**失敗策略**：LLM_CHAIN 中任一 provider 驗證失敗 → raise `RuntimeError` → server 啟動失敗。Planka / DB 失敗同樣 raise。這是有意設計的硬性保護，確保運算資源不被浪費在確定失敗的流程上。

### D3：各 provider 的連通測試方式

| Provider | 測試方式 |
|----------|----------|
| `claude-cli` | `claude auth status --json`，確認 `loggedIn: true` |
| `gemini-cli` | `gemini --version`，returncode == 0 且有輸出 |
| `codex-cli` | `codex --version`，returncode == 0 |
| `opencode-cli` | `opencode --version`，returncode == 0 |
| `claude-api` | `ANTHROPIC_API_KEY` 存在即視為可用（不做實際 API 呼叫） |
| `gemini-api` | `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` 存在即視為可用 |
| Planka JWT | `GET {PLANKA_URL}/api/v1/users/me` with `Authorization: Bearer {PLANKA_TOKEN}`，期望 200 |
| Database | `SELECT 1` via psycopg3 |

**替代考量**：API provider 做實際呼叫 → 不選，會消耗 quota 且增加啟動時間；環境變數存在就夠做後續失敗診斷。

### D4：Spec Review Graph — Author/Reviewer/Synthesizer 迴圈

**State 設計：**
```python
class SpecReviewState(TypedDict):
    project_id: str
    card_id: str
    spec_path: str
    participants: list[str]       # ["claude-cli", "gemini-cli", "codex-cli"]
    current_round: int            # 0-indexed
    total_rounds: int             # len(participants) + 1
    current_spec_md: str          # 只有 author/synthesizer 更新
    review_notes: list[dict]      # {"participant", "round", "status", "questions"}
    status: str                   # "in_progress" | "pass" | "need_update" | "abort"
    questions: list[str]          # 最終待問 user 的問題
```

**角色判斷（在 spec_review_round 節點內）：**
```
round == 0              → author     → participants[0]，initial prompt
0 < round < total - 1  → reviewer   → participants[round]，review prompt
round == total - 1      → synthesizer → participants[0]，synthesize prompt
```

**Graph 結構：**
```
START → spec_review_init → spec_review_round → spec_finalize → END
                                ↑        ↓
                                └────────┘  (current_round < total_rounds - 1)
```

**Conditional edge logic：**
```python
def _route_review(state):
    if state["current_round"] < state["total_rounds"] - 1:
        return "spec_review_round"
    return "spec_finalize"
```

**Resume 能力：** PostgresSaver 在每個 node 執行完後寫入 checkpoint。若 `spec_review_round` 在 round 2 失敗，checkpoint 保存了 `current_round=1`（上輪完成後的值）與所有 `review_notes`。Resume 後 LangGraph 從 `current_round=2` 重跑，不重複 round 0/1。

### D5：Spec review graph 的 thread_id 對應

Spec review graph 使用 `project_id` 作為 `thread_id`（與 research graph 相同）。由於兩個 graph 使用不同的 graph 物件（不同 StateGraph instance），PostgresSaver 不會混淆 checkpoint（LangGraph checkpoint 以 graph + thread_id 為 key）。

**替代考量**：用 `{project_id}:spec_review` 作 thread_id → 不選，增加複雜度，且 LangGraph 已透過 graph 物件隔離。

### D6：Prompt role 擴充

`spec_clarifier.py` 的 `_load_prompt(role)` 由目前的 `primary`/`secondary` 改為支援：

| Role | Prompt 檔案 | 用途 |
|------|------------|------|
| `initial` | `spec_agent_initial.txt` | 讀 spec.md，產出初稿（原 primary 邏輯） |
| `review` | `spec_agent_review.txt` | 讀 current spec，列出審查意見，不改稿 |
| `synthesize` | `spec_agent_synthesize.txt` | 讀 current spec + review_notes，寫最終版 + status file |

`review` role 的 LLM 不寫 `reviewed_spec_*.md`，只寫 `review_notes_round{N}.txt`，供 synthesizer 讀取。`spec_review_round` 節點根據角色決定要讀哪個輸出檔案並更新 state。

Gemini 的 `STRICT RULE` prefix 邏輯移入 `run_spec_agent()`，依 provider name 判斷是否需要注入（`if "gemini" in provider_name`）。

## Risks / Trade-offs

**[Risk] Spec review graph 的 thread_id 與 research graph 衝突**
→ Mitigation：LangGraph checkpoint 以 `(graph_id, thread_id)` 為複合 key，兩個 StateGraph 物件的 graph_id 不同，不會衝突。實作時加 unit test 驗證兩個 graph 各自的 checkpoint 互不干擾。

**[Risk] Preflight 驗證通過後 LLM 在流程中途登出**
→ Mitigation：此情況仍會在 `run_once()` 層拋出 `RuntimeError`，LangGraph 節點失敗，可 resume。Preflight 只保護「啟動時確定有問題」的情況，不保證執行期間的穩定性。這在設計意圖內。

**[Risk] `review` role 的 LLM 沒有按照 protocol 只輸出意見，而是改了稿**
→ Mitigation：`review` prompt 明確約束「只列出意見，不輸出修改後的 spec」，且 `spec_review_round` 節點不讀 `current_spec_md` 的更新（reviewer 即使寫了也被忽略），`review_notes` 才是唯一輸出管道。

**[Risk] Synthesizer 看到大量審查意見後輸出超長 spec，超過 token 限制**
→ Mitigation：reviewer 的 prompt 約束意見長度（每條不超過 3 句）。若 synthesizer 真的超時，LangGraph 節點失敗，可 resume 重試。長遠可加 `max_review_note_chars` 截斷。

**[Trade-off] Preflight 硬性阻止啟動 vs 降級啟動**
選擇硬性阻止。理由：系統的核心功能（spec review、research graph）100% 依賴 LLM 與 DB，降級模式只能提供 health endpoint，對業務毫無價值。讓 server 清楚地啟動失敗比悄悄降級更容易診斷。

## Migration Plan

1. 部署新版本前，確認 `VOLUME_BASE_DIR` 目錄存在（preflight cache 寫入位置）
2. 既有進行中的 spec review（`review_in_progress` flag 為 true 的 project）在升級後會以舊背景任務方式失效；需手動清除 flag 後重新觸發（移卡片回 Spec Pending Review）
3. 舊版 `reviewed_spec_primary.md` / `reviewed_spec_secondary.md` 命名格式不變，`initial` / `synthesize` role 沿用相同輸出檔名，不影響既有的 spec 解析邏輯
4. Rollback：還原 `server.py`、`llm_svc.py`、`spec_clarifier.py` 即可；`spec_review_graph.py` 與 `llm_preflight.py` 為純新增，刪除不影響其他模組

## Open Questions

- `claude auth status --json` 在不同版本的 Claude Code CLI 輸出格式是否一致？需在實作時驗證實際輸出。
- `review` prompt 的設計（格式、約束）需要實際測試後調整；第一版可先用簡單格式，確保 synthesizer 能解析即可。
