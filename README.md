# Agentic Research Workflow Engine

> 以 LangGraph 驅動的通用研究循環框架，內建 Planka 看板整合與 PostgreSQL 狀態持久化。

---

## 專案簡介

本框架將**研究流程自動化**與**人工審核**解耦：

- **Phase 1（Spec Review）**：使用者用 `spec.md` 描述研究需求，LLM 檢查模糊欄位並提問，問題解決後自動進入 Phase 2
- **Phase 2（Research Loop）**：LangGraph 自動執行 plan→implement→test→analyze 循環，每 N 個 PASS loop 後在 Planka 等待人工決策

業務邏輯透過 **Plugin** 插入，框架本身無業務耦合。

---

## Planka 看板狀態機

```
Planning  →  Spec Pending Review  →  Verify  →  Review  →  Done / Failed
   ↑               ↑                    ↑
   │ (issues)      │ (./start.sh)       │ (loop review: continue / replan)
   └───────────────┘                    └──────────────────────────────────
```

| 欄位 | 觸發 | 說明 |
|------|------|------|
| **Planning** | `agentic-research init` | 專案建立，等待使用者撰寫 spec.md |
| **Spec Pending Review** | `./start.sh` | LLM 正在審查 spec |
| **Verify** | spec 無問題 | Research loop 自動執行中 |
| **Review** | 每 N 個 PASS loop | 等待人工決策（continue / replan / terminate） |
| **Done** | loop_review `terminate` | 完成 |
| **Failed** | 系統錯誤 | 異常終止 |

> **Planning 欄位 = 人工等待區**：spec 有問題時卡片退回此欄，使用者修改 spec.md 後再執行 `./start.sh`。

---

## 完整使用者流程

```
─── 一次性（per machine）────────────────────────────────────────────
  agentic-research setup
    → 啟動 4 個 Docker 服務
    → 互動式 LLM 憑證設定
    → 建立 Planka board + 6 個欄位

─── 每個新專案─────────────────────────────────────────────────────
  agentic-research init <name>
    → 建立本地目錄 + spec.md 模板
    → 呼叫 /project/init API → Planka 建立卡片在 Planning

─── 使用者撰寫需求──────────────────────────────────────────────────
  編輯 spec.md（填入假說、資產、參數範圍、績效門檻）

─── Spec 審查（可多輪）─────────────────────────────────────────────
  ./start.sh
    → POST /start → LLM 審查 spec
    → 有問題：寫出 spec.clarified.md，Planka 卡片退回 Planning
    → 無問題：Planka 進入 Verify，Research loop 自動啟動

  （如有問題）填入 spec.clarified.md 的 Answer 欄位
  ./resume.sh
    → POST /resume → 帶著答案重新啟動 graph → Verify

─── Research Loop（全自動）─────────────────────────────────────────
  plan → implement → test → analyze
    FAIL → revise → implement（自動）
    PASS → summarize → record_metrics
      每 N loops → Planka 卡片移至 Review [INTERRUPT]

─── Loop Review（人工決策）─────────────────────────────────────────
  方式 A：curl POST /resume
  方式 B：Planka webhook（將卡片拖曳）

  continue  → 繼續下一個 loop（Planka → Verify）
  replan    → 圖內部重新 plan，繼續執行（Planka 留在 Verify）
  terminate → 結束研究（Planka → Done）
```

---

## 技術架構

```
agentic-research/
├── framework/                  ← 框架核心
│   ├── graph.py                  StateGraph, ResearchState, routers
│   ├── plugin_interface.py       ResearchPlugin ABC
│   ├── plugin_registry.py        @register, resolve(), discover_plugins()
│   ├── notify.py                 notify_planka_node（loop_review interrupt）
│   ├── spec_clarifier.py         spec.md 解析、LLM 審查、clarified.md 產生
│   ├── db/
│   │   ├── connection.py         psycopg_pool ConnectionPool
│   │   └── queries.py            projects / loop_metrics / checkpoint_decisions
│   └── api/
│       └── server.py             FastAPI: /project/init /start /resume /planka-webhook
│
├── projects/                   ← Plugin 實作
│   ├── dummy/plugin.py           測試用（deterministic FAIL→PASS，無 plan_review interrupt）
│   ├── sample/plugin.py          超參數搜尋模擬（含 MLflow）
│   ├── demo/plugin.py            展示用
│   └── quant_alpha/              量化策略 plugin（LLM + rule-based fallback）
│
├── agentic_research/           ← 主機端 CLI（pip install .）
│   ├── cli.py                    Typer app: setup / init / start / resume
│   ├── setup_cmd.py              一次性 infra 設定 + Planka board 建立
│   ├── init_cmd.py               建立本地目錄 + /project/init API 呼叫
│   ├── project_cmds.py           start / resume 指令邏輯
│   └── templates/                spec.md, start.sh/bat, resume.sh/bat, credentials.yaml
│
├── db/migrations/
│   └── 001_business_schema.sql   業務表 DDL
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml        postgres + mlflow + planka + framework-api
└── pyproject.toml                entry_point: agentic-research
```

### 服務清單

| 服務 | 用途 | Port |
|------|------|------|
| `postgres` | LangGraph checkpoints + 業務 schema | 5432 |
| `framework-api` | FastAPI 工作流程引擎 | 7001 |
| `mlflow` | 實驗追蹤 UI（可選） | 5000 |
| `planka` | HITL 看板介面 | 7002 |

### 資料庫 Schema

```
LangGraph 自動建立：
  checkpoints, checkpoint_blobs, checkpoint_migrations

業務表（001_business_schema.sql）：
  projects             — 專案註冊
  loop_metrics         — 每個 PASS loop 指標
  checkpoint_decisions — 人工決策審計軌跡
```

---

## Quick Start

### 前置需求

- Docker & Docker Compose
- Python 3.11+
- Git

### 0. PyPI 安裝（推薦）

```bash
pip install agentic-research
agentic-research --help
```

### 1. 從 Source 安裝（開發用）

```bash
git clone <this-repo>
cd agentic-research

# 建立虛擬環境（建議）
python -m venv venv
source venv/bin/activate      # Windows: venv\Scripts\activate

# 安裝 agentic-research 指令
pip install -e .

# 確認安裝成功
agentic-research --help
```

### 2. 全域設定（一次）

```bash
agentic-research setup
```

互動式完成：LLM 憑證設定 → Docker 服務啟動 → Planka board 建立。

### 3. 建立新專案

```bash
agentic-research init my-strategy
cd my-strategy
```

Planka 上自動出現「My Strategy」卡片，位於 **Planning**。

### 4. 撰寫研究需求

編輯 `spec.md`（填入假說、Universe、績效門檻等）。

### 5. 提交審查

```bash
./start.sh       # Windows: start.bat
```

- 無問題 → 卡片自動進入 **Verify**，research loop 開始執行。
- 有問題 → `spec.clarified.md` 寫出，卡片退回 **Planning**。

```bash
# 填完 spec.clarified.md 的答案後
./resume.sh      # Windows: resume.bat
```

### 6. Loop Review

每 N 個 PASS loop 後，Planka 卡片移至 **Review**，等待人工決策：

```bash
# 繼續
curl -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{"project_id":"my-strategy","decision":{"action":"continue"}}'

# 帶方向繼續（圖內部 replan，自動繼續）
curl -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{"project_id":"my-strategy","decision":{"action":"replan","notes":"try smaller window"}}'

# 結束
curl -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{"project_id":"my-strategy","decision":{"action":"terminate"}}'
```

---

## 實作自己的 Plugin

在 `projects/<name>/plugin.py` 繼承 `ResearchPlugin`：

```python
from framework.plugin_interface import ResearchPlugin
from framework.plugin_registry import register

@register
class MyPlugin(ResearchPlugin):
    name = "my_plugin"

    def plan_node(self, state: dict) -> dict:
        # 讀：loop_goal, last_checkpoint_decision
        # 寫：implementation_plan, needs_human_approval=False
        ...

    def implement_node(self, state: dict) -> dict:
        # 直接執行，無 interrupt
        ...

    def test_node(self, state: dict) -> dict:
        # 寫：test_metrics
        ...

    def analyze_node(self, state: dict) -> dict:
        # 寫：last_result = "PASS" | "FAIL" | "TERMINATE"
        ...

    def revise_node(self, state: dict) -> dict: ...
    def summarize_node(self, state: dict) -> dict: ...

    def get_review_interval(self) -> int:
        return 5  # 每 5 個 PASS loop 觸發 loop_review
```

> **注意**：Plugin 的 implement_node 不應自行呼叫 `interrupt()`。
> 唯一的 LangGraph interrupt 在框架的 `notify_planka_node`（loop_review）。

框架透過 `discover_plugins()` 自動掃描 `projects/*/plugin.py`。

---

## Roadmap

| Phase | 狀態 | 內容 |
|-------|------|------|
| Phase 0 | ✅ | 最小基礎設施（Docker、DB schema、Plugin ABC） |
| Phase 1 | ✅ | Core Graph + CLI |
| Phase 2 | ✅ | HITL（loop_review interrupt、/resume API） |
| Phase 2.5 | ✅ | 業務 Schema（record_metrics、checkpoint_decisions） |
| Phase 3 | ✅ | 真實 Plugin（QuantAlphaPlugin） |
| Phase 4 | ✅ | 系統強化（connection pool、plugin 自動掃描） |
| Phase 5 | ✅ | 文件與範例（SamplePlugin、MLflow、PLUGIN_SPEC.md） |
| Phase 6 | ✅ | Spec 審查流程（spec.md → LLM review → spec.clarified.md） |
| Phase 7 | ✅ | Planka 狀態機（Planning→Spec Pending Review→Verify→Review→Done/Failed） |

## 技術債

| # | 項目 |
|---|------|
| TD-01 | 通知機制（email / Slack）— 目前只靠 Planka 卡片移動 |
| TD-02 | `notify.py` 的 `PLANKA_REVIEW_LIST_ID` 邏輯待清理 |
| TD-03 | 支援 plugin 自帶 Docker image |
