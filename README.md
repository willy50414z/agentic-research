# Agentic Research Workflow Engine

> 以 LangGraph 驅動的通用研究循環框架，內建人工審核機制與 PostgreSQL 狀態持久化。

---

## 專案簡介

本專案是一個**與業務邏輯完全解耦**的 Agentic 工作流程引擎。框架負責驅動研究循環、持久化狀態、處理人工中斷；業務邏輯（例如量化交易回測、NLP 實驗）透過 **Plugin** 插入，不需修改框架本身。

```
START → plan → implement → test → analyze
                                     │ FAIL  → revise → implement（不中斷）
                                     │ PASS  → summarize → record_metrics
                                     │              │ 每 N loops → Loop Review ⏸
                                     │              │ continue  → plan
                                     │ TERMINATE → END
```

每個 loop 開始前有 **Plan Review** 中斷點，每 N 個 PASS loop 後有 **Loop Review** 中斷點，兩者皆透過 CLI 或 HTTP API 恢復。

---

## 核心特性

| 特性 | 說明 |
|------|------|
| **Plugin 架構** | `ResearchPlugin` ABC 定義介面，業務邏輯放在 `projects/<name>/plugin.py`，框架零業務耦合 |
| **持久化狀態** | LangGraph `PostgresSaver` 作為 checkpointer，任何時間點中斷都能從 PostgreSQL 恢復 |
| **兩層 HITL** | Plan Review（每次 plan 後）+ Loop Review（每 N 次 PASS 後），可 approve / reject / replan / terminate |
| **自動指標記錄** | 框架節點 `record_metrics` 在每個 PASS loop 後自動寫入 `loop_metrics` 表，plugin 不需自行處理 |
| **審計軌跡** | 所有人工決策寫入 `checkpoint_decisions` 表，完整保留 action / notes / 時間戳 |
| **LLM 整合** | `framework/llm_agent` 支援 Claude / Gemini / Codex 等 CLI 工具，內建 fallback 機制（LLM 未安裝時自動切換規則邏輯） |
| **Planka 可選** | 設定 `PLANKA_TOKEN` 後，Loop Review 自動建立看板卡片；不設定仍可正常運作 |
| **MLflow 可選** | 設定 `MLFLOW_TRACKING_URI` 後，plugin 可呼叫 `mlflow.log_*` 記錄指標；UI 在 `http://localhost:5000` |
| **輕量 API** | FastAPI ~80 行，提供 `/resume`（CLI 呼叫）與 `/planka-webhook`（看板事件）兩個端點 |

---

## 技術架構

```
agentic-research/
├── framework/                  ← 框架核心（無業務邏輯）
│   ├── graph.py                  build_graph()、ResearchState、router 函式
│   ├── plugin_interface.py       ResearchPlugin ABC
│   ├── plugin_registry.py        @register 裝飾器、resolve()、list_plugins()、discover_plugins()
│   ├── notify.py                 notify_planka_node（含 interrupt()）
│   ├── tag_parser.py             _extract_tag()（解析 LLM 輸出 tag）
│   ├── llm_agent/
│   │   ├── llm_target.py         LLMTarget enum（CLAUDE / GEMINI / CODEX / ...）
│   │   └── llm_svc.py            run_once(target, prompt) — subprocess 呼叫 LLM CLI
│   ├── db/
│   │   ├── connection.py         psycopg_pool ConnectionPool（min=1, max=5）
│   │   └── queries.py            projects / loop_metrics / checkpoint_decisions CRUD
│   └── api/
│       └── server.py             FastAPI /resume + /planka-webhook
│
├── projects/                   ← Plugin 實作（業務邏輯在此）
│   ├── sample/plugin.py          SamplePlugin（★ 新手起點：超參數搜尋模擬，含 MLflow）
│   ├── dummy/plugin.py           DummyPlugin（測試用，固定 FAIL→PASS 邏輯）
│   ├── demo/plugin.py            DemoPlugin（展示用，詳細 logging 的 momentum 策略模擬）
│   └── quant_alpha/              QuantAlphaPlugin（Phase 3 真實量化研究 plugin）
│       ├── plugin.py               完整六節點實作，LLM 呼叫 + rule-based fallback
│       ├── backtest.py             純 Python 回測引擎（RSI / MA crossover / breakout）
│       └── prompts/                LLM prompt 模板（plan / analyze / revise / summarize）
│
├── docs/
│   ├── AGENT_CONTEXT.md          ★ 給 AI Agent 的系統說明（簡短，可貼入 prompt）
│   └── PLUGIN_SPEC.md            ★ Plugin 開發完整規格（六節點合約、狀態讀寫表）
│
├── cli/main.py                 ← CLI 入口（start / status / approve / plugins）
├── main.py                     ← FastAPI 應用入口（uvicorn 啟動點）
├── docker/
│   ├── Dockerfile                Python 3.11 image（含 Node.js 供 LLM CLI）
│   └── docker-compose.yml        PostgreSQL + LangGraph App + MLflow + Planka
├── db/migrations/
│   └── 001_business_schema.sql   業務表 DDL
├── requirements.txt            ← 釘版直接依賴
├── artifacts/                  ← 本地 artifact 存放（loop 報告 .md 等）
├── graph_viz.html              ← LangGraph 工作流程視覺化（Mermaid，瀏覽器直開）
└── demo_run.py                 ← 全自動 end-to-end 展示腳本（使用 DemoPlugin）
```

### 服務清單

| 服務 | 用途 | Port |
|------|------|------|
| `postgres` | LangGraph checkpointer + 業務 schema | 5432 |
| `langgraph-engine` | FastAPI + 工作流程引擎 | 7001 |
| `mlflow` | 實驗追蹤 UI（可選） | 5000 |
| `planka` | 可選 HITL 看板介面 | 7002 |

### 資料庫 Schema

```
LangGraph 自動建立：
  checkpoints、checkpoint_blobs、checkpoint_migrations

業務表（001_business_schema.sql）：
  projects             — 專案 / thread 註冊
  loop_metrics         — 每個 PASS loop 的指標（win_rate、alpha_ratio 等）
  checkpoint_decisions — 人工決策審計軌跡
```

---

## Quick Start

### 前置需求

- Docker & Docker Compose
- Git

### 1. 啟動服務

```bash
git clone <this-repo>
cd agentic-research

# 複製環境設定（視需要修改 VOLUME_BASE_DIR）
cp .env.example .env   # 如已有 .env 可跳過

docker compose up -d
docker compose ps      # 確認服務都是 healthy（postgres / langgraph-engine / mlflow）
```

> `.env` 已內建 `COMPOSE_FILE=docker/docker-compose.yml`，不需加 `-f` 參數。

---

## 完整互動演示：Sample Plugin

> `SamplePlugin` 模擬超參數搜尋，預設行為：
> - **Loop 0**：lr=0.01 → accuracy=0.61 → **FAIL** → revise → lr=0.001 → accuracy=0.86 → **PASS**
> - **Loop 1**：lr=0.001 batch=128 → accuracy=0.77 → **PASS** → **⏸ Loop Review**（review_interval=2）
> - **Loop 2**：lr=0.0003 batch=64 → accuracy=0.91 → **PASS** → **⏸ Loop Review**

每一步都等待你輸入指令，讓你親眼看到整個 HITL 流程。

---

### Step 1 — 啟動研究（跑到第一個 Plan Review 暫停）

```bash
docker exec agentic-langgraph python cli/main.py start \
  --project sample_001 \
  --plugin sample \
  --goal "find optimal learning rate and batch size" \
  --review-interval 2
```

Log 輸出（節錄）：

```
[START] Project 'sample_001' | Plugin 'sample'
        Goal: find optimal learning rate and batch size

[Sample] plan    loop=0  config=lr=0.01 batch=32 epochs=10

--- Project: sample_001 ---
  loop_index          : 0
  next_nodes          : ['implement']

[INTERRUPT] Waiting for human input:
  checkpoint: plan_review
  loop_index: 0
  plan: {'loop': 0, 'config': {'lr': 0.01, 'batch_size': 32, 'epochs': 10}, ...}

  Approve or reject this plan.
    approve : python cli/main.py approve --project sample_001 --action approve
    reject  : python cli/main.py approve --project sample_001 --action reject --reason "..."

[PAUSED] Run `approve` to resume.
```

---

### Step 2 — Plan Review：審核計畫並批准

```bash
docker exec agentic-langgraph python cli/main.py approve \
  --project sample_001 --action approve
```

圖繼續執行：

```
[Sample] implement  loop=0 attempt=1  running experiment...
[Sample] test    loop=0 attempt=1  lr=0.01 batch=32 epochs=10  accuracy=0.6139
[Sample] analyze  ✘ FAIL — accuracy=0.6139 < 0.75 → will revise config
[Sample] revise  ↻ config changed: lr 0.01→0.001  batch 32→64
[Sample] test    loop=0 attempt=2  lr=0.001 batch=64 epochs=20  accuracy=0.8630
[Sample] analyze  ✔ PASS — accuracy=0.8630 ≥ 0.75  lr=0.001  batch=64
[Sample] summarize  loop=0  Loop 0 PASS — accuracy=0.8630  lr=0.001  batch=64

[PAUSED] Run `approve` to resume.    ← Loop 1 的 Plan Review
```

---

### Step 3 — 查看目前狀態與 Loop 0 指標

```bash
docker exec agentic-langgraph python cli/main.py status --project sample_001
```

```
--- Project: sample_001 ---
  loop_index          : 1
  last_result         : PASS
  loop_count_since_review: 1
  next_nodes          : ['implement']

[INTERRUPT] Waiting for human input:
  checkpoint: plan_review  loop_index: 1
  ...

--- Loop History ---
  Loop  0: PASS   win_rate=-     alpha=-     reason: Loop 0 PASS — accuracy=0.8630  lr=0.001  batch=64

[PAUSED] Run `approve` to resume.
```

---

### Step 4 — 繼續 Loop 1

```bash
docker exec agentic-langgraph python cli/main.py approve \
  --project sample_001 --action approve
```

```
[Sample] test    loop=1 attempt=1  lr=0.001 batch=128 epochs=30  accuracy=0.7674
[Sample] analyze  ✔ PASS — accuracy=0.7674 ≥ 0.75  lr=0.001  batch=128
[Sample] summarize  loop=1  Loop 1 PASS — accuracy=0.7674  lr=0.001  batch=128
```

**loop_count_since_review = 2 → Loop Review 觸發！**

```
[notify_planka] Loop 2 review checkpoint — Waiting for human decision.

[INTERRUPT] Waiting for human input:
  checkpoint: loop_review
  loop_index: 2
  summary: Loop 1 PASS — accuracy=0.7674 ...
  instruction: Resume with: {'action': 'continue'|'replan'|'terminate', 'notes': '...'}

[PAUSED] Run `approve` to resume.
```

---

### Step 5 — Loop Review：選擇繼續 / Replan / 結束

**選項 A — 繼續下一個 loop：**

```bash
docker exec agentic-langgraph python cli/main.py approve \
  --project sample_001 --action continue
```

**選項 B — 修改方向後繼續（replan）：**

```bash
docker exec agentic-langgraph python cli/main.py approve \
  --project sample_001 --action replan \
  --notes "try even smaller batch size to reduce overfitting"
```

下一個 loop 的 plan_node 會在 `loop_goal` 末尾加上 `[REVISED: ...]` 並選用新 config。

**選項 C — 結束研究：**

```bash
docker exec agentic-langgraph python cli/main.py approve \
  --project sample_001 --action terminate
```

```
[Sample] plan: human requested terminate.
[DONE] Graph has completed (reached END).
```

---

### Step 6 — 透過 Planka 看板操作 Loop Review（可選）

若已設定 `PLANKA_TOKEN` 與 `PLANKA_REVIEW_LIST_ID`：

1. Loop Review 觸發時，框架自動在 Planka 建立「`[sample_001] Loop X Review`」卡片
2. 卡片內含摘要與 `thread_id: sample_001`
3. 將卡片拖曳到：
   - **Approved** → 等同 `--action continue`
   - **Rejected** → 等同 `--action terminate`
4. Webhook 即時通知 `POST /planka-webhook` → 圖自動繼續

---

### Step 7 — 透過 MLflow 查看實驗指標

MLflow UI 已隨 `docker compose up` 啟動，瀏覽器開啟：

```
http://localhost:5000
```

每次 `test_node` 執行都會建立一個 MLflow run，記錄：

| 參數 (params) | 指標 (metrics) |
|--------------|----------------|
| `lr` | `accuracy` |
| `batch_size` | |
| `epochs` | |

**查看方式：**

1. 左側選擇 Experiment（名稱 = project_id，例如 `sample_001`）
2. 點開任意 Run 可看到 params / metrics 記錄
3. 勾選多個 Run → Compare → 畫出 accuracy 折線圖

> 若不需要 MLflow，將 `.env` 中 `MLFLOW_TRACKING_URI` 設為空字串即可，plugin 會自動跳過日誌記錄。

---

### 通用 CLI 指令

```bash
# 啟動任意 plugin 的新專案
docker exec agentic-langgraph python cli/main.py start \
  --project <id> --plugin <name> --goal "<目標>" --review-interval <n>

# 查看狀態與 loop 歷史
docker exec agentic-langgraph python cli/main.py status --project <id>

# Plan Review 決策
docker exec agentic-langgraph python cli/main.py approve --project <id> --action approve
docker exec agentic-langgraph python cli/main.py approve --project <id> --action reject --reason "<原因>"

# Loop Review 決策
docker exec agentic-langgraph python cli/main.py approve --project <id> --action continue
docker exec agentic-langgraph python cli/main.py approve --project <id> --action replan --notes "<修改方向>"
docker exec agentic-langgraph python cli/main.py approve --project <id> --action terminate

# 透過 HTTP API 操作（等效）
curl -s -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{"project_id":"sample_001","decision":{"action":"approve"}}'

# 列出已註冊的 plugin
docker exec agentic-langgraph python cli/main.py plugins

# 全自動 Demo（不需人工干預，驗證環境用）
docker exec agentic-langgraph python demo_run.py
```

### 觀看工作流程圖

直接用瀏覽器開啟（不需伺服器）：

```
graph_viz.html
```

---

## 實作自己的 Plugin

> 完整規格請參閱 `docs/PLUGIN_SPEC.md`（適合直接給 AI Agent 閱讀後協助串接）。

在 `projects/<your_name>/plugin.py` 建立一個繼承 `ResearchPlugin` 的類別：

```python
from framework.plugin_interface import ResearchPlugin
from framework.plugin_registry import register

@register
class MyPlugin(ResearchPlugin):
    name = "my_plugin"

    def plan_node(self, state: dict) -> dict:
        # 讀：loop_goal, last_checkpoint_decision
        # 寫：implementation_plan, needs_human_approval=True
        ...

    def implement_node(self, state: dict) -> dict:
        # 當 needs_human_approval=True 時呼叫 interrupt() 等待 Plan Review
        ...

    def test_node(self, state: dict) -> dict:
        # 寫：test_metrics（dict，key 對應 loop_metrics 欄位）
        ...

    def analyze_node(self, state: dict) -> dict:
        # 寫：last_result = "PASS" | "FAIL" | "TERMINATE"
        ...

    def revise_node(self, state: dict) -> dict:
        # FAIL 後修正策略，更新 implementation_plan
        ...

    def summarize_node(self, state: dict) -> dict:
        # 產出報告，loop_index+1，attempt_count=0
        ...

    def get_review_interval(self) -> int:
        return 3   # 每 3 個 PASS loop 觸發一次 Loop Review
```

框架透過 `discover_plugins()` **自動掃描** `projects/*/plugin.py`，不需手動 import。

### LLM 整合（選填）

```python
from framework.llm_agent.llm_svc import run_once
from framework.llm_agent.llm_target import LLMTarget

try:
    response = run_once(LLMTarget.CLAUDE, prompt, timeout=120)
except FileNotFoundError:
    # Claude CLI 未安裝，使用 rule-based fallback
    response = my_fallback_logic()
```

### 節點狀態讀寫規範

| 節點 | 讀取 | 寫入 |
|------|------|------|
| `plan_node` | `loop_goal`, `last_checkpoint_decision` | `implementation_plan`, `needs_human_approval=True` |
| `implement_node` | `implementation_plan`, `needs_human_approval` | `needs_human_approval=False`, `artifacts`（append） |
| `test_node` | `implementation_plan` | `test_metrics`（dict）, `attempt_count+1` |
| `analyze_node` | `test_metrics` | `last_result`, `last_reason` |
| `revise_node` | `implementation_plan`, `last_reason`, `attempt_count` | `implementation_plan`（修正版） |
| `summarize_node` | `loop_goal`, `last_reason`, `artifacts` | `last_reason`, `artifacts`, `loop_index+1`, `loop_count_since_review+1`, `attempt_count=0` |

---

## 已知技術債

目前無未解決的技術債。所有 Phase 0-4 項目已完成。

---

## Roadmap

| Phase | 狀態 | 內容 |
|-------|------|------|
| Phase 0 | ✅ 完成 | 最小基礎設施（Docker、DB schema、Plugin ABC） |
| Phase 1 | ✅ 完成 | Core Graph + CLI（graph.py、DummyPlugin、start/status/plugins） |
| Phase 2 | ✅ 完成 | HITL（interrupt 機制、/resume API、CLI approve） |
| Phase 2.5 | ✅ 完成 | 業務 Schema 完善（record_metrics、checkpoint_decisions） |
| Phase 3 | ✅ 完成 | 真實 Plugin（QuantAlphaPlugin：純 Python 回測 + LLM 整合 + fallback） |
| Phase 4 | ✅ 完成 | 系統強化（connection pool、FAIL/TERMINATE 指標記錄、plugin 自動掃描、goal 長度保護） |
| Phase 5 | ✅ 完成 | 文件與範例（SamplePlugin + MLflow + docs/AGENT_CONTEXT.md + docs/PLUGIN_SPEC.md） |