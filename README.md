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
| **輕量 API** | FastAPI ~80 行，提供 `/resume`（CLI 呼叫）與 `/planka-webhook`（看板事件）兩個端點 |

---

## 技術架構

```
agentic-research/
├── framework/                  ← 框架核心（無業務邏輯）
│   ├── graph.py                  build_graph()、ResearchState、router 函式
│   ├── plugin_interface.py       ResearchPlugin ABC
│   ├── plugin_registry.py        @register 裝飾器、resolve()、list_plugins()
│   ├── notify.py                 notify_planka_node（含 interrupt()）
│   ├── tag_parser.py             _extract_tag()（解析 LLM 輸出 tag）
│   ├── llm_agent/
│   │   ├── llm_target.py         LLMTarget enum（CLAUDE / GEMINI / CODEX / ...）
│   │   └── llm_svc.py            run_once(target, prompt) — subprocess 呼叫 LLM CLI
│   ├── db/
│   │   ├── connection.py         psycopg3 連線
│   │   └── queries.py            projects / loop_metrics / checkpoint_decisions CRUD
│   └── api/
│       └── server.py             FastAPI /resume + /planka-webhook
│
├── projects/                   ← Plugin 實作（業務邏輯在此）
│   ├── dummy/plugin.py           DummyPlugin（測試用，固定 FAIL→PASS 邏輯）
│   ├── demo/plugin.py            DemoPlugin（展示用，詳細 logging 的 momentum 策略模擬）
│   └── quant_alpha/              QuantAlphaPlugin（Phase 3 真實研究 plugin）
│       ├── plugin.py               完整六節點實作，LLM 呼叫 + rule-based fallback
│       ├── backtest.py             純 Python 回測引擎（RSI / MA crossover / breakout）
│       └── prompts/                LLM prompt 模板（plan / analyze / revise / summarize）
│
├── cli/main.py                 ← CLI 入口（start / status / approve / plugins）
├── main.py                     ← FastAPI 應用入口（uvicorn 啟動點）
├── docker/
│   ├── Dockerfile                Python 3.11 image（含 Node.js 供 LLM CLI）
│   └── docker-compose.yml        PostgreSQL + LangGraph App + Planka
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
docker compose ps      # 確認三個服務都是 healthy
```

> `.env` 已內建 `COMPOSE_FILE=docker/docker-compose.yml`，不需加 `-f` 參數。

### 2. 執行 Demo（全自動）

```bash
# 使用 DemoPlugin 執行全自動 end-to-end demo（3 個 loop，含 FAIL→revise 示範）
docker exec agentic-langgraph python demo_run.py
```

輸出範例：

```
  demo_run    ══════════════════════════════════════
  demo_run      AGENTIC RESEARCH WORKFLOW — LIVE DEMO
  demo_run    ══════════════════════════════════════
  ...
  demo.plugin    NODE ▶  PLAN    │  loop=0
  demo.plugin        Strategy    : RSI-20 momentum
  demo_run    ⏸  PLAN REVIEW (loop 0)  — AUTO-APPROVING
  demo.plugin    NODE ▶  TEST    │  loop=0  attempt=1
  demo.plugin        ✘ FAIL — win_rate=0.4800 < 0.55 → will revise params
  demo.plugin    NODE ▶  TEST    │  loop=0  attempt=2
  demo.plugin        ✔ PASS — win_rate=0.5600 ≥ 0.55
  demo_run    ⏸  LOOP REVIEW  — AUTO-TERMINATE
  demo_run    GRAPH COMPLETED — reached END
```

### 3. 執行 QuantAlpha Plugin（手動操作）

QuantAlpha 是 Phase 3 實作的真實量化研究 plugin，使用純 Python 回測引擎驗證 RSI / MA / breakout 策略。

```bash
# 啟動新研究（運行到 Plan Review 中斷點）
docker exec agentic-langgraph python cli/main.py start \
  --project qa_001 \
  --plugin quant_alpha \
  --goal "find alpha in RSI momentum strategies" \
  --review-interval 2

# 審核計畫並批准
docker exec agentic-langgraph python cli/main.py approve \
  --project qa_001 --action approve

# 查看 loop 歷史與指標
docker exec agentic-langgraph python cli/main.py status \
  --project qa_001
```

典型 log 輸出（含 FAIL→revise 路徑標示）：

```
[QuantAlpha] implement  train win_rate=0.5333  n_trades=15
[QuantAlpha] test       win_rate=0.5000  alpha=1.0902  drawdown=0.1280
[QuantAlpha] analyze    ✘ FAIL — Failed: win_rate=0.5000 < 0.55 → will revise params
[QuantAlpha] revise     ↻ params changed: lookback 14→10  entry 0.35→0.30  exit 0.55→0.60
[QuantAlpha] test       win_rate=0.6000  alpha=1.1448  drawdown=0.1149
[QuantAlpha] analyze    ✔ PASS — win_rate=0.6000 ≥ 0.55  alpha=1.1448 ≥ 1.0  drawdown=0.1149 ≤ 0.20
```

### 4. 通用 CLI 指令

```bash
# 啟動任意 plugin 的新專案
docker exec agentic-langgraph python cli/main.py start \
  --project <id> --plugin <name> --goal "<目標>" --review-interval <n>

# 查看狀態與 loop 歷史
docker exec agentic-langgraph python cli/main.py status --project <id>

# Plan Review 決策
docker exec agentic-langgraph python cli/main.py approve \
  --project <id> --action approve
docker exec agentic-langgraph python cli/main.py approve \
  --project <id> --action reject --reason "<原因>"

# Loop Review 決策
docker exec agentic-langgraph python cli/main.py approve \
  --project <id> --action continue
docker exec agentic-langgraph python cli/main.py approve \
  --project <id> --action replan --notes "<修改方向>"
docker exec agentic-langgraph python cli/main.py approve \
  --project <id> --action terminate

# 列出已註冊的 plugin
docker exec agentic-langgraph python cli/main.py plugins
```

### 5. 觀看工作流程圖

直接用瀏覽器開啟（不需伺服器）：

```
graph_viz.html
```

---

## 實作自己的 Plugin

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

接著在 `cli/main.py` 與 `main.py` 加入 import：

```python
import projects.my_plugin.plugin  # noqa: F401
```

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

| 項目 | 說明 | 預計修正 |
|------|------|---------|
| 單一 DB 連線 | `framework/db/connection.py` 使用模組層級單一連線 | Phase 4：改用 connection pool |
| Plugin 手動 import | `cli/main.py` 需逐一 import 各 plugin | Phase 4：改用 importlib 自動掃描 |
| FAIL loop 無指標記錄 | `loop_metrics` 只記錄 PASS loop | Phase 4 |

---

## Roadmap

| Phase | 狀態 | 內容 |
|-------|------|------|
| Phase 0 | ✅ 完成 | 最小基礎設施（Docker、DB schema、Plugin ABC） |
| Phase 1 | ✅ 完成 | Core Graph + CLI（graph.py、DummyPlugin、start/status/plugins） |
| Phase 2 | ✅ 完成 | HITL（interrupt 機制、/resume API、CLI approve） |
| Phase 2.5 | ✅ 完成 | 業務 Schema 完善（record_metrics、checkpoint_decisions） |
| Phase 3 | ✅ 完成 | 真實 Plugin（QuantAlphaPlugin：純 Python 回測 + LLM 整合 + fallback） |
| Phase 4 | 待開發 | 可選增強（Planka webhook、MinIO、Langfuse、connection pool、plugin 自動掃描） |