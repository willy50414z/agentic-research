## Context

目前框架使用 LangGraph `StateGraph` + `PostgresSaver` 作為研究工作流執行引擎，並以 `LLMProviderFactory` 管理多種 LLM provider（CLI 與 API），以自訂 XML tag parsing（`tag_parser.py`）解析 LLM 輸出。這帶入大量維護負擔：provider 實作各有 subprocess 管理邏輯，tag parsing 容易因輸出格式不穩而失敗。

替換方案：
- **Planka column** 作為人工介面狀態機
- **DB `workflow_step` 欄位** 追蹤系統執行步驟
- **`llm_eval.evaluate()`** 統一 LLM 呼叫與 outcome routing
- DB schema 全部重建，不保留舊資料

## Goals / Non-Goals

**Goals:**
- 移除所有 LangGraph / langchain-core / langsmith 依賴
- 移除 `LLMProviderFactory`、`tag_parser`、`spec_clarifier`（舊實作）
- 以 `llm_eval.evaluate()` 統一所有 LLM 呼叫，Outcome 宣告式定義取代手動 tag parsing
- 每個執行步驟為原子操作：crash 後可直接從 `workflow_step` 重新執行同一步驟
- Planka column 僅反映需人工介入的狀態，內部執行步驟不暴露為 column
- 將 Planka `Verify` column 改名為 `Executing`
- DB schema 全部重建（drop + create）

**Non-Goals:**
- 保留或 migrate 舊 DB 資料
- 引入新的外部依賴（除 llm_eval 外）
- 修改 `ResearchPlugin` ABC 的 node 方法外部簽名

## Decisions

### 決策 1：以 `llm_eval.evaluate()` 取代 LLMProviderFactory + tag_parser

**選擇：** 所有 LLM 呼叫改用 `llm_eval.evaluate(target, purpose, outcomes)`，LLM 透過寫入 `status_<name>` signal file 表達 outcome，框架自動 route 至對應 callback。

**原來模式：**
```python
llm_fn = LLMProviderFactory.build("claude-cli")
raw = llm_fn(prompt)
result = tag_parser.parse(raw, "<RESULT>")  # 脆弱，格式不穩
```

**新模式（在 node 函式內）：**
```python
from llm_eval import evaluate, Outcome, JobResult, LLMTarget

updates = {}

evaluate(
    target=LLMTarget.CLAUDE,
    purpose=prompt,
    outcomes=[
        Outcome("pass",      "測試通過",   output_files=["reason.txt"], callback=lambda r: updates.update({"last_result": "PASS",      "last_reason": r.files["reason.txt"]})),
        Outcome("fail",      "需要修正",   output_files=["reason.txt"], callback=lambda r: updates.update({"last_result": "FAIL",      "last_reason": r.files["reason.txt"]})),
        Outcome("terminate", "研究終止",   output_files=["reason.txt"], callback=lambda r: updates.update({"last_result": "TERMINATE", "last_reason": r.files["reason.txt"]})),
    ],
)
return updates
```

**原因：** `evaluate()` 將 subprocess 管理、quota retry、workspace 隔離、outcome routing 全部封裝。LLM 以寫 status file 表達意圖，比 XML tag parsing 更穩定（binary signal，無歧義）。

**安裝方式：** `pip install -e E:\code\agent-cli\`（editable install，路徑固定）

---

### 決策 2：LLM target 設定方式

**選擇：** 環境變數 `LLM_TARGET`（值為 `CLAUDE`、`GEMINI`、`CODEX`、`OPENCODE`、`COPILOT`），對應 `LLMTarget` enum。

**原因：** 舊的 `LLM_CHAIN`（逗號分隔多 provider）是為了 spec review 的多 LLM round 設計，但 `llm_eval` 是同步執行，多 LLM chain 可在 step 函式中依序呼叫 `evaluate()` 實作，不需要 provider factory。

**映射表：**
```
LLM_TARGET=CLAUDE    → LLMTarget.CLAUDE
LLM_TARGET=GEMINI    → LLMTarget.GEMINI
LLM_TARGET=CODEX     → LLMTarget.CODEX
LLM_TARGET=OPENCODE  → LLMTarget.OPENCODE
LLM_TARGET=COPILOT   → LLMTarget.COPILOT
```

---

### 決策 3：以 DB `workflow_step` 取代 LangGraph checkpointer

**選擇：** `projects` table 新增 `workflow_step varchar(64)` 欄位，每個 step 完成後 upsert。State dict 本身繼續存於 `projects.config`（JSONB）。

**原因：** LangGraph PostgresSaver 序列化完整 state 的目的是 crash recovery。`workflow_step` 欄位達到相同效果：crash 後重啟，webhook 重觸發，讀取欄位值重跑同一 step。

**DB 重建策略：** 全部 drop + create，不做 ALTER TABLE migration。舊 LangGraph checkpoint tables 同時清除。

---

### 決策 4：以純 Python 函式取代 StateGraph

**選擇：** `framework/workflow.py` 定義 step dispatcher，每次 `Executing` webhook 觸發讀 `workflow_step`，呼叫對應 step 函式。Step 函式直接呼叫 plugin node 方法。

```
webhook: Executing → read workflow_step → dispatch to run_<step>()
                                              ↓
                                    plugin.analyze_node(state)
                                              ↓
                                    write workflow_step = next_step
                                    move card / stay in Executing
```

---

### 決策 5：HITL 改由 Planka column 移動控制

**選擇：** 需人工介入時主動移卡至 `Review`，記錄 `paused_at` 到 `projects.config`。人工移回 `Executing` 時 webhook 觸發，讀取 `paused_at` 繼續執行。

**原因：** LangGraph interrupt() 依賴 checkpoint 機制，移除後不可用。Planka column 本身是狀態機，card 位置即現在的 phase。

---

### 決策 6：Spec 審查改為 step-based，以 llm_eval 執行每個 round

**選擇：** `spec_review_graph.py` 刪除，以 `framework/spec_review.py` 取代。每個 round（initial、synthesize）為獨立的 `evaluate()` 呼叫，Outcome 宣告 `reviewed_spec.md`（PASS）或 `questions.txt`（需澄清）。

```python
evaluate(
    target=LLMTarget.CLAUDE,
    purpose=spec_review_prompt,
    outcomes=[
        Outcome("pass",        "Spec 完整",   output_files=["reviewed_spec.md"], callback=on_pass),
        Outcome("need_update", "Spec 有缺漏", output_files=["questions.txt"],    callback=on_need_update),
    ],
)
```

## Risks / Trade-offs

**[Risk] `evaluate()` 是同步阻塞呼叫，長時間執行會佔用 FastAPI worker thread**
→ Mitigation：所有 step 函式已在背景 thread（`BackgroundTasks`）執行，不影響 API 可用性。llm_eval 的 `timeout` 參數預設 1800s，與現有行為一致。

**[Risk] `llm_eval` editable install 路徑（`E:\code\agent-cli\`）為機器相對路徑**
→ Mitigation：開發環境記錄於 README，CI 環境需確保路徑存在或用絕對路徑。未來可考慮發布為 wheel。

**[Risk] Crash 在 step 執行中途，`evaluate()` workspace 尚未清理**
→ Mitigation：llm_eval 在 callback 完成後無條件清理 workspace，crash 後殘留目錄無副作用，下次執行重新建立新 workspace。

**[Risk] LLM 不寫 status file（`evaluate()` 拋 RuntimeError）**
→ Mitigation：在 `on_exception` handler 移卡至 `Failed`，記錄錯誤 comment。

## Migration Plan

1. Drop 所有 DB tables，以新 schema 重建（workflow_step 欄位包含在內）
2. 安裝 llm_eval：`pip install -e E:\code\agent-cli\`
3. 建立 `framework/workflow.py`（step dispatcher）
4. 建立 `framework/spec_review.py`（llm_eval 版 spec 審查）
5. 修改 `framework/api/server.py`：移除 LangGraph，改接 workflow.py
6. 修改 `framework/plugin_interface.py`：node docstring 更新說明 llm_eval 使用方式
7. 刪除 `framework/graph.py`、`spec_review_graph.py`、`llm_providers.py`、`tag_parser.py`、`spec_clarifier.py`
8. 更新 `requirements.txt`
9. Planka board：`Verify` → `Executing`

## Open Questions

- Plugin 作者（quant_alpha 等）的 node 實作目前可能直接使用 `LLMProviderFactory`，是否需要提供 migration guide 或輔助 wrapper？建議在 `plugin_interface.py` docstring 加入 llm_eval 使用範例。
- `LLM_TARGET` 是否需支援 per-step 設定（plan 用 Claude，analyze 用 Gemini）？目前先以單一 env var，後續可擴充為 `LLM_TARGET_PLAN`、`LLM_TARGET_ANALYZE` 等。
