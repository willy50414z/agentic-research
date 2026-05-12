## Why

LangGraph 在本專案中僅作為 Python if/elif routing 和 Postgres 狀態持久化的包裝層，帶入 7 個額外套件（langgraph、langchain-core、langsmith 等）卻未提供對應的架構價值。同時，現有的 `LLMProviderFactory` 維護多種 provider 實作（CLI + API），造成重複的 subprocess 管理與輸出解析邏輯。以 Planka column 作為人工介面狀態機、DB `workflow_step` 欄位追蹤系統執行步驟，並引入 `llm_eval` 統一管理 LLM 呼叫與 outcome routing，可大幅簡化技術棧。DB schema 全部重建，不保留舊資料。

## What Changes

- **移除** `framework/graph.py`（StateGraph 研究工作流）
- **移除** `framework/spec_review_graph.py`（StateGraph Spec 審查工作流）
- **移除** `framework/llm_providers.py`（多 provider 工廠）
- **移除** `framework/tag_parser.py`（XML tag 輸出解析）
- **移除** `framework/spec_clarifier.py`（舊 spec 審查邏輯，以 llm_eval 版本取代）
- **移除** LangGraph 相關套件：`langgraph`、`langgraph-checkpoint`、`langgraph-checkpoint-postgres`、`langgraph-prebuilt`、`langgraph-sdk`、`langchain-core`、`langsmith`
- **新增** `llm_eval` 套件（`pip install -e E:\code\agent-cli\`）作為統一 LLM 呼叫層
- **新增** `projects.workflow_step` 欄位至 DB schema（全部重建，不做 migration）
- **修改** `framework/api/server.py`：移除所有 LangGraph import，改以 step-based 執行函式替代 graph.invoke
- **修改** `framework/plugin_interface.py`：node 方法內部以 `llm_eval.evaluate()` 呼叫 LLM，取代 `LLMProviderFactory` + tag parsing
- **修改** Planka board：將 `Verify` column 改名為 `Executing`
- **修改** `requirements.txt`：移除 LangGraph 套件，新增 `llm_eval` editable install
- 執行步驟（`plan → implement → test → analyze → summarize`）改由 DB `workflow_step` 追蹤，不暴露為 Planka column

## Capabilities

### New Capabilities

- `step-based-workflow`: 以純 Python 函式替代 LangGraph StateGraph，每個執行步驟為一個原子操作；步驟狀態存於 DB `workflow_step`，Planka column 僅保留人工介入所需的狀態（Planning / Spec Pending Review / Executing / Review / Done / Failed）
- `llm-eval-integration`: 以 `llm_eval.evaluate()` 統一所有 LLM 呼叫，透過 `Outcome` 宣告式定義可能的結果與對應的輸出檔案，取代 `LLMProviderFactory` + XML tag parsing 模式

### Modified Capabilities

- `workflow-graph`: 移除 StateGraph / PostgresSaver / interrupt() 機制，改為 step-based 執行引擎
- `hitl-interrupts`: 人工介入不再透過 LangGraph interrupt() 觸發，改為 Planka column 移動（卡片在 Review = 暫停，移回 Executing = 繼續）
- `api-server`: webhook routing 新增 `Executing` column handler，移除 `Command(resume=...)` 邏輯
- `persistence-schema`: DB schema 全部重建，`projects` table 新增 `workflow_step` 欄位

## Impact

**移除的套件（requirements.txt）：**
- `langgraph`、`langgraph-checkpoint`、`langgraph-checkpoint-postgres`
- `langgraph-prebuilt`、`langgraph-sdk`、`langchain-core`、`langsmith`

**新增的套件：**
- `llm_eval`（`pip install -e E:\code\agent-cli\`，editable install）

**移除的檔案：**
- `framework/graph.py`
- `framework/spec_review_graph.py`
- `framework/llm_providers.py`
- `framework/tag_parser.py`
- `framework/spec_clarifier.py`（以 llm_eval 版本重寫取代）

**修改的檔案：**
- `framework/api/server.py`
- `framework/plugin_interface.py`
- `framework/db/`（schema 全部重建）
- `requirements.txt`
- `docker-compose.yml`

**Plugin interface 簽名不變：** `ResearchPlugin` ABC 的 node 方法簽名 `(state: dict) -> dict` 維持不變；plugin 內部改用 `llm_eval.evaluate()` 呼叫 LLM，但框架層介面對 plugin 作者透明。
