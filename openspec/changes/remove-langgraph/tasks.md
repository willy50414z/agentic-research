## 1. 環境與依賴

- [x] 1.1 執行 `pip install -e E:\code\agent-cli\` 安裝 llm_eval
- [x] 1.2 從 `requirements.txt` 移除：`langgraph`、`langgraph-checkpoint`、`langgraph-checkpoint-postgres`、`langgraph-prebuilt`、`langgraph-sdk`、`langchain-core`、`langsmith`
- [x] 1.3 在 `requirements.txt` 或 `pyproject.toml` 記錄 llm_eval 的 editable install 路徑（`-e E:\code\agent-cli\`）
- [x] 1.4 更新 `docker-compose.yml`：移除 LangGraph 相關環境變數，新增 `LLM_TARGET` 環境變數
- [x] 1.5 確認 `LLM_TARGET` preflight check：無效值時啟動失敗並記錄 error

## 2. DB Schema 重建

- [x] 2.1 編寫 `framework/db/schema.sql`（或 Python 建表腳本），包含 `workflow_step VARCHAR(64) DEFAULT NULL` 欄位
- [x] 2.2 Drop 所有現有 DB tables（包含 LangGraph checkpoint tables）
- [x] 2.3 以新 schema 重建所有 tables
- [x] 2.4 新增 `framework/db/queries.py` 的 `get_workflow_step(project_id)` 與 `set_workflow_step(project_id, step)` 函式

## 3. llm_eval 整合層

- [x] 3.1 建立 `framework/llm_target.py`：讀取 `LLM_TARGET` env var，回傳對應 `LLMTarget` enum 值
- [x] 3.2 定義各 step 的標準 Outcome 集合（analyze: pass/fail/terminate、spec_review: pass/need_update）
- [x] 3.3 確認 `evaluate()` 的 `on_exception` handler 統一移卡至 `Failed` 並留 Planka comment

## 4. Step Dispatcher 模組

- [x] 4.1 建立 `framework/workflow.py`，定義 `dispatch_step(project_id, state)` 入口：讀 `workflow_step` → 呼叫對應 step 函式
- [x] 4.2 實作 `run_plan(state, plugin, sink)` — 呼叫 `plugin.plan_node(state)`（plugin 內部用 llm_eval），完成後寫 `workflow_step = "implement"`
- [x] 4.3 實作 `run_implement(state, plugin, sink)` — 呼叫 `plugin.implement_node(state)`，完成後寫 `workflow_step = "test"`
- [x] 4.4 實作 `run_test(state, plugin, sink)` — 呼叫 `plugin.test_node(state)`，完成後寫 `workflow_step = "analyze"`
- [x] 4.5 實作 `run_analyze(state, plugin, sink)` — 呼叫 `plugin.analyze_node(state)`，依 `last_result` 路由：PASS → `workflow_step = "summarize"`；FAIL（未超限）→ `workflow_step = "implement"`；TERMINATE / 超限 → 移卡至 `Review`
- [x] 4.6 實作 `run_summarize(state, plugin, sink)` — 呼叫 `plugin.summarize_node(state)`，完成後移卡至 `Done`，`workflow_step = "done"`
- [x] 4.7 實作 max_loops 邊界判斷（`attempt_index >= max_loops` → 覆寫為 TERMINATE）
- [x] 4.8 實作 HITL 暫停：`needs_human_approval = True` → 記錄 `paused_at` 至 `projects.config`，移卡至 `Review`

## 5. Spec 審查改寫

- [x] 5.1 建立 `framework/spec_review.py`，以 `llm_eval.evaluate()` 實作 initial round（Outcome: pass / need_update）
- [x] 5.2 實作 synthesize round（`workflow_step = "spec_review_synthesize"`）：`evaluate()` + Outcome `reviewed_spec.md` 或 `questions.txt`
- [x] 5.3 `spec_review.py` 完成後決定移卡方向：pass → `Executing`（`workflow_step = "plan"`）；need_update → `Planning`（Planka comment 貼問題）
- [x] 5.4 刪除 `framework/spec_review_graph.py`
- [x] 5.5 刪除 `framework/spec_clarifier.py`（邏輯已由 spec_review.py 取代）

## 6. API Server 更新

- [x] 6.1 將 `_COL_VERIFY = "Verify"` 改為 `_COL_EXECUTING = "Executing"`，更新所有引用
- [x] 6.2 重寫 `Executing` column webhook handler：讀 `workflow_step` → 呼叫 `dispatch_step()` 背景任務
- [x] 6.3 新增重複執行防護：同一 project 已有背景任務時回應 `{"status": "skipped"}`
- [x] 6.4 移除 `POST /resume` endpoint 與 `_run_resume_bg` 函式
- [x] 6.5 移除所有 `from langgraph.*`、`from langchain_core.*`、`from framework.llm_providers` import

## 7. 舊檔案清理

- [x] 7.1 刪除 `framework/graph.py`
- [x] 7.2 刪除 `framework/llm_providers.py`
- [x] 7.3 刪除 `framework/tag_parser.py`

## 8. Plugin Interface 文件更新

- [x] 8.1 更新 `framework/plugin_interface.py` docstring：移除 LangGraph 相關說明，新增 llm_eval 使用範例（analyze_node 示範 evaluate() 呼叫與 Outcome 定義）
- [x] 8.2 更新 `projects/demo/plugin.py` 作為 llm_eval 呼叫的參考實作

## 9. Planka Board 更新

- [ ] 9.1 在 Planka board 將 `Verify` column 改名為 `Executing`

## 10. 測試更新

- [x] 10.1 移除 `tests/` 中直接測試 `build_graph()`、`LLMProviderFactory`、`tag_parser` 的測試案例
- [x] 10.2 新增 step dispatcher 單元測試：各 step 函式的 PASS / FAIL / TERMINATE routing 正確
- [x] 10.3 新增 `workflow_step` 狀態轉換測試：確認每個 step 完成後 DB 欄位正確更新
- [x] 10.4 新增 llm_eval Outcome routing 測試（mock `evaluate()`，確認 callback 正確更新 state）
