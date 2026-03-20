# Research Workflow Design
_Last updated: 2026-03-20_

---

## 設計目標

讓外部使用者能透過一份結構化需求文件啟動一個自主研究流程，
LLM 負責將模糊需求補全為可執行規格，framework 負責跑到完成。

---

## 指令總覽

| 指令 | 說明 | 執行次數 |
|------|------|----------|
| `agentic-research setup` | 建立全域基礎設施 + 互動式 LLM 設定 | 一次，per machine |
| `agentic-research init <name>` | 建立單一研究專案目錄與模板 | 每個新專案一次 |
| `./start.sh` | 初始化專案：register DB + Planka card + 生成 spec.clarified.yaml | 每個專案一次 |
| `./resume.sh` | 推進 LangGraph 狀態（可多次執行，對應澄清循環） | 每次推進時 |

---

## Global Infra（`~/.agentic-research/docker-compose.yml`）

4 個 service 全部必要，無預設註解：

```
postgres        ← LangGraph checkpoints + business tables（projects, loop_metrics, checkpoint_decisions）
mlflow          ← experiment tracking
planka          ← workflow UI + card-move webhook
framework-api   ← FastAPI (main.py)，/resume + /planka-webhook，內建 scheduler
```

`setup` 執行時：
- 啟動 4 個 service（`docker compose up -d`）
- 互動式設定 LLM 憑證（claude → codex → gemini → local，依序確認）
- 結果寫入 `~/.agentic-research/.env`
- 在 Planka 建立 board + columns

### Planka 看板欄位

```
Clarifying  →  In Progress  →  Review  →  Done / Failed
```

### `~/.agentic-research/` 全域結構

```
~/.agentic-research/
├── .env                    ← LLM 設定 + infra 連線（不 commit）
├── docker-compose.yml      ← 4 service
├── teardown.sh             ← docker compose down
└── data/
    ├── postgres/
    └── mlflow/
```

---

## 專案結構（`agentic-research init <name>` 產生）

```
./quant-ma-v1/
├── spec.md                  ← 使用者填寫研究需求（Markdown，活文件）
├── spec.clarified.md        ← LLM 提問 + 使用者填寫答案（Markdown Q&A）
├── credentials.yaml         ← 外部工具憑證路徑（不存實際 key）
├── start.sh / start.bat     ← 第一次初始化用
├── resume.sh / resume.bat   ← 推進 LangGraph 狀態用
├── artifacts/               ← 回測結果
└── logs/                    ← 執行紀錄
```

**spec 檔案設計原則**：
- 使用者用 **Markdown** 溝通（`spec.md`、`spec.clarified.md`）
- AI agent 內部用 **YAML/dict**（由 LLM 從 md 轉換，使用者無感）

`start.sh` 和 `resume.sh` 直接呼叫 framework-api，無需 per-project Docker container：

```bash
# start.sh
FRAMEWORK_API_URL="${FRAMEWORK_API_URL:-http://localhost:7001}"
agentic-research start --spec "$(pwd)/spec.md" --out "$(pwd)/spec.clarified.md" --api "$FRAMEWORK_API_URL"
```

```bash
# resume.sh
FRAMEWORK_API_URL="${FRAMEWORK_API_URL:-http://localhost:7001}"
agentic-research resume --spec-clarified "$(pwd)/spec.clarified.md" --spec "$(pwd)/spec.md" --api "$FRAMEWORK_API_URL"
```

---

## 完整 User Journey

```
┌─ 一次性（per machine）──────────────────────────────────┐
│  agentic-research setup                                 │
│    → 啟動 4 個 infra service                            │
│    → 互動式 LLM 設定 → ~/.agentic-research/.env         │
│    → 建立 Planka board + columns                        │
└────────────────────────────────────────────────────────┘

┌─ 每個新專案──────────────────────────────────────────────┐
│  agentic-research init quant-ma-v1                      │
│    → 建專案目錄 + spec.yaml 模板                         │
│    → 產生 start.sh / resume.sh（+ .bat）                │
└────────────────────────────────────────────────────────┘

user 編輯 spec.yaml（填寫假說、資產、參數範圍、績效門檻等）

┌─ 初始化（每個專案一次）─────────────────────────────────┐
│  ./start.sh                                             │
│    → 條件式啟動 infra（若未跑）                         │
│    → docker run cli/main.py start：                     │
│        create_project() 寫入 DB                         │
│        在 Planka 建立 card → "Clarifying"               │
│        規則驗證（必填欄位/格式）                         │
│        LLM 語意分析（模糊欄位提問）                      │
│        產出 spec.clarified.yaml                         │
│          （頂部保存原始 spec 快照，防止回溯困難）         │
│    → container 退出                                     │
└────────────────────────────────────────────────────────┘

┌─ 迭代澄清循環（可多輪）────────────────────────────────┐
│                                                        │
│  user 編輯 spec.clarified.yaml，填寫 answer            │
│                                                        │
│  ./resume.sh                                           │
│    → docker run cli/main.py resume：                   │
│                                                        │
│      Step A：查 LangGraph state                        │
│        有 pending questions                            │
│          → 從 checkpoint 取出                          │
│          → 寫入 spec.clarified.yaml（更新問題）         │
│          → 印出提示，退出                              │
│        無 pending                                      │
│          → 讀 spec.clarified.yaml answers              │
│          → POST /resume { answers: {...} }             │
│                                                        │
│      Step B：framework-api background task             │
│        LangGraph "review spec" node（LLM 評估）        │
│        ┌─ 仍有模糊                                     │
│        │    → 存 questions 到 checkpoint               │
│        │    → Planka 留在 "Clarifying"                 │
│        │    → 等下一次 resume.sh                       │
│        └─ 無問題                                       │
│             → LangGraph 自動繼續（無 interrupt）        │
│             → Phase 2 research loop 啟動               │
│             → Planka → "In Progress"（同步）           │
│                                                        │
└────────────────────────────────────────────────────────┘

┌─ Phase 2：Research Loop（自動執行）────────────────────┐
│                                                        │
│  LangGraph graph（framework-api 背景運行）：           │
│    plan_node → implement_node → test_node              │
│             → analyze_node → [PASS/FAIL]               │
│                                                        │
│  FAIL → 下一個 loop（最多 max_loops 次）               │
│  PASS / review_interval 到達                           │
│    → loop review interrupt                             │
│    → Planka 自動移卡 → "Review"（同步）                │
│                                                        │
└────────────────────────────────────────────────────────┘

┌─ Loop Review（user 決策）──────────────────────────────┐
│                                                        │
│  user 在 Planka 移卡到 Approved / Rejected             │
│    → /planka-webhook → /resume → background task       │
│    Approved → 繼續下一輪 loop                          │
│    Rejected → 終止，Planka → "Failed"                  │
│                                                        │
│  或 CLI 直接呼叫：                                     │
│    curl POST /resume { action: "continue" }            │
│                                                        │
└────────────────────────────────────────────────────────┘

┌─ 補漏機制──────────────────────────────────────────────┐
│  framework-api 內建 scheduler（定期執行）               │
│    → 查 Planka "Ready" column                          │
│    → 找無對應 LangGraph checkpoint 的 project          │
│    → 自動觸發啟動                                      │
└────────────────────────────────────────────────────────┘
```

---

## spec.md 模板（量化策略，使用者填寫）

```markdown
# Research Spec: MA Crossover Research

## Hypothesis
快慢均線交叉可以捕捉趨勢動能，在日線級別的美股上產生正期望值。

## Universe
- Instruments: AAPL, MSFT, GOOG
- Asset class: equity
- Frequency: daily

## Data
- Source: yfinance
- Training period: 2018-01-01 to 2022-12-31
- Test period (OOS): 2023-01-01 to 2024-12-31

## Signals
- Entry: SMA(close, fast_period) crosses above SMA(close, slow_period)
- Exit: SMA(close, fast_period) crosses below SMA(close, slow_period)

## Optimization
- Method: Grid search
- Parameters:
  - fast_period: 5, 10, 15, 20, 30
  - slow_period: 40, 60, 80, 100

## Performance Thresholds
| Metric | Target |
|--------|--------|
| Sharpe Ratio | ≥ 1.0 |
| Max Drawdown | ≤ 20% |

## Settings
- Plugin: quant_strategy
- Review every: 5 loops
- Max loops: 30
```

> AI agent 讀取 spec.md 後自動轉換為內部 YAML，使用者無需關心格式細節。

---

## spec.clarified.md 結構（Q&A Markdown）

```markdown
# Spec Clarifications

**Project**: quant-ma-v1
**Generated**: 2026-03-20 14:32 UTC

> Fill in each **Answer** below, then run `./resume.sh`.

---

## 1. `signals.entry`

**Original value**
```
SMA(close, fast_period) crosses above SMA(close, slow_period)
```

**Question**
交叉確認方式？(1) 單根 K 棒收盤 (2) N 根連續收盤

**Answer**
<!-- fill in below -->
單根 K 棒收盤確認

---

## 2. `performance.sharpe_ratio.min`

**Original value**
```
1.0
```

**Question**
Sharpe ratio 是年化還是 per-period？

**Answer**
<!-- fill in below -->

---
```

---

## 架構設計決策

### Spec → ResearchState
- ResearchState 加 `spec: dict` key，存完整結構化 spec
- Framework 在 start 時注入，plugin 透過 `state.spec` 取用
- User 不定義 flow，framework 固定流程

### Spec 補全策略
- 規則驗證（必填欄位、格式）→ 先跑，不通過直接報錯
- LLM 只處理語意模糊（如 signal 描述的具體實作方式）
- 規則通過的欄位不再詢問 LLM

### 狀態管理
- Source of truth：LangGraph checkpoint（postgres）
- Planka 卡片位置反映當前狀態，由 framework-api 同步維護
- 無額外 status column，checkpoint 位置即狀態

### `/resume` endpoint（framework/api/server.py）
```
POST /resume
  body: { project_id, decision: { action, answers?, confirmed? } }

內部邏輯：
  有 checkpoint → graph.invoke(Command(resume=decision))（background task）
  無 checkpoint + confirmed=true → graph.invoke(initial_state)（background task）
  無 checkpoint + 未確認 → 409 { status: "confirmation_required" }
```

### LLM 優先順序
```
claude → codex → gemini → opencode（local）
```
適用於 spec review（Phase 1）與 research loop（Phase 2）。

---

## Docker 架構

```
Global stack（~/.agentic-research/docker-compose.yml，常駐）:
  postgres / mlflow / planka / framework-api

Per-project（start.sh / resume.sh 觸發，short-lived）:
  agentic-research:latest
  volumes: $(pwd):/workspace
  network: agentic-research_default
  container 跑完自動消失（--rm）
```

---

## 技術債清單

| # | 項目 | 原因 |
|---|------|------|
| TD-01 | 通知機制（email/Slack） | 目前只靠 Planka 卡片移動，無主動通知 |
| TD-02 | Fat container 拆成 sidecar（freqtrade 等） | 目前直接裝進 image |
| TD-03 | 支援 plugin-defined Docker image | 讓 plugin 自帶執行環境 |
| TD-04 | Global compose 改為 per-project compose | 多 project 並行有潛在衝突風險 |
