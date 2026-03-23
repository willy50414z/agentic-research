# Maintainer Guide — Agentic Research Framework

> 給維護人員與新進成員的快速上手指南。
> 版本：0.3.0 | 最後更新：2026-03-23

---

## 目錄

1. [系統概覽](#1-系統概覽)
2. [服務架構](#2-服務架構)
3. [Planka Webhook 流程](#3-planka-webhook-流程)
4. [完整研究工作流程](#4-完整研究工作流程)
5. [專案結構](#5-專案結構)
6. [新增 Plugin](#6-新增-plugin)
7. [本地開發啟動](#7-本地開發啟動)
8. [常見問題排查](#8-常見問題排查)

---

## 1. 系統概覽

本系統是一個 **Human-in-the-Loop (HITL) 自動化研究框架**，以 [LangGraph](https://langchain-ai.github.io/langgraph/) 驅動研究迴圈，並以 [Planka](https://planka.app/) 看板作為人工審核介面。

核心概念：
- 每個「研究任務」對應 Planka 看板上的一張卡片
- 使用者透過移動卡片欄位來觸發或控制研究流程
- 系統自動執行 plan → implement → test → analyze 迴圈
- 每隔 N 次成功迴圈，暫停並通知人類審核是否繼續

---

## 2. 服務架構

```
┌──────────────────────────────────────────────────────────┐
│  Docker Compose (deploy/docker-compose.local.yml)        │
│                                                          │
│  ┌─────────────────┐   ┌──────────────────────────────┐  │
│  │  Planka :7002   │   │  Framework API :7001 (→8000) │  │
│  │  (HITL 看板)    │──▶│  FastAPI / LangGraph         │  │
│  └─────────────────┘   └──────────────────────────────┘  │
│           │                         │                    │
│           ▼                         ▼                    │
│  ┌─────────────────┐   ┌──────────────────────────────┐  │
│  │  PostgreSQL:5432│   │  MinIO :9000                 │  │
│  │  (DB + 檢查點)  │   │  (spec/artifact 儲存)        │  │
│  └─────────────────┘   └──────────────────────────────┘  │
│                                                          │
│  ┌─────────────────┐                                     │
│  │  MLflow :5000   │  (選用，實驗追蹤)                   │
│  └─────────────────┘                                     │
└──────────────────────────────────────────────────────────┘
```

---

## 3. Planka Webhook 流程

### Webhook Endpoint

```
POST /planka-webhook
```

定義於 `framework/api/server.py:357`

### 觸發條件

Planka 在卡片發生 `cardUpdate` 事件（即卡片被移動到不同欄位）時，呼叫此 endpoint。

### 欄位路由邏輯

| 目標欄位 | 觸發動作 |
|---|---|
| `Spec Pending Review` | 啟動雙 LLM Spec 審核（`_run_spec_review_bg`） |
| `Verify` / `Done` | 恢復圖執行，action = `continue` |
| `Failed` | 終止研究，action = `terminate` |
| 其他欄位 | 忽略 |

### Webhook Payload 解析流程

```
Planka 發送 POST /planka-webhook
    ↓
1. 過濾：只處理 event == "cardUpdate"
2. 確認是真的換欄（current_list_id != prev_list_id）
3. 從 card.description 解析 thread_id（格式：thread_id: <project_id>）
4. 依目標欄位分流：
   ├─ Spec Pending Review → 排入背景任務做 Spec 審核
   ├─ Verify / Done       → 排入背景任務恢復 LangGraph 執行
   └─ Failed              → 排入背景任務終止研究
5. 立即回傳 {"status": "ok/spec_review_queued/ignored/error"}
```

> **注意**：所有重量級處理都是 `background_tasks`（非同步），Webhook 本身快速回傳不阻塞 Planka。

---

## 4. 完整研究工作流程

### Planka 看板欄位狀態機

```
Planning
   │
   │ 使用者拖曳卡片（或 ./start.sh）
   ▼
Spec Pending Review ──→ [雙 LLM 審核 spec.md]
   │                          │
   │ 審核通過                  │ 審核不通過
   ▼                          ▼
Verify                    Planning
   │                    （加入問題說明）
   │ LangGraph 自動執行
   ▼
[研究迴圈]
plan → implement → test → analyze
   ├─ FAIL → revise → implement（繼續）
   └─ PASS → summarize → 計數
                 │
                 │ 達到 review_interval 次 PASS
                 ▼
              Review ──→ 人類審核
                 │
         ┌───────┼───────┐
         ▼       ▼       ▼
      continue replan terminate
         │       │       │
         ▼       ▼       ▼
      Verify  Verify    Done
      (繼續)  (重規劃)  (結束)
```

### 步驟詳細說明

| 步驟 | 操作者 | 說明 |
|---|---|---|
| 1. 初始化 | 使用者 | `agentic-research init <project-name>` 建立本地目錄與 spec.md 範本 |
| 2. 編寫規格 | 使用者 | 編輯 `spec.md`，描述研究目標與設定 |
| 3. 提交審核 | 使用者 | 執行 `./start.sh` 或 `POST /start`，卡片移至 Spec Pending Review |
| 4. Spec 審核 | 系統（LLM） | 雙 LLM 驗證規格，通過則移至 Verify |
| 5. 研究迴圈 | 系統 | 自動執行 plan/implement/test/analyze 迴圈 |
| 6. Loop Review | 人類 | 每 N 次成功後，系統通知並等待人類決策 |
| 7. 完成 | 系統 | 卡片移至 Done，研究結束 |

---

## 5. 專案結構

```
agentic-research/
├── main.py                        # Uvicorn 進入點（自動探索 plugins）
├── cli/
│   └── main.py                    # CLI 指令：init、init-planka-board
│
├── framework/
│   ├── api/
│   │   └── server.py              # ★ FastAPI 路由（含 /planka-webhook）
│   ├── graph.py                   # LangGraph 工作流程定義
│   ├── plugin_interface.py        # Plugin 抽象基底類別
│   ├── plugin_registry.py         # Plugin 自動探索與註冊
│   ├── notify.py                  # Loop Review 中斷通知節點
│   ├── planka.py                  # Planka HTTP client（PlankaSink）
│   ├── spec_clarifier.py          # 雙 LLM Spec 審核邏輯
│   ├── minio_client.py            # MinIO artifact 儲存
│   └── db/
│       ├── connection.py          # PostgreSQL 連線池
│       └── queries.py             # 資料庫 CRUD
│
├── projects/                      # Plugin 實作目錄
│   ├── dummy/plugin.py            # 測試用（固定 FAIL→PASS）
│   ├── sample/plugin.py           # 超參數搜索範例（含 MLflow）
│   ├── demo/plugin.py             # 示範用
│   └── quant_alpha/               # 量化策略研究
│
└── deploy/
    ├── Dockerfile
    ├── docker-compose.local.yml
    ├── schema.sql                  # 資料表定義
    └── .env                        # 環境變數設定
```

### 關鍵 API 路由

| Method | Path | 說明 |
|---|---|---|
| `POST` | `/planka-webhook` | Planka 卡片事件接收器 |
| `GET`  | `/health` | 健康檢查 |
| `POST` | `/project/init` | 初始化新專案（CLI 呼叫） |
| `POST` | `/start` | 觸發 Spec 審核 |
| `POST` | `/resume` | 恢復暫停中的圖執行 |

### 資料庫 Tables

| Table | 用途 |
|---|---|
| `projects` | 專案 metadata + JSONB config |
| `loop_metrics` | 每次迴圈的 PASS/FAIL 紀錄 |
| `checkpoint_decisions` | 人類審核決策稽核日誌 |
| LangGraph tables | 圖狀態檢查點（自動建立） |

---

## 6. 新增 Plugin

在 `projects/<your-plugin>/plugin.py` 建立以下結構：

```python
from framework.plugin_interface import ResearchPlugin
from framework.plugin_registry import register

@register
class MyPlugin(ResearchPlugin):
    name = "my-plugin"  # 對應 spec.md 的 plugin 欄位

    def plan_node(self, state): ...
    def implement_node(self, state): ...
    def test_node(self, state): ...
    def analyze_node(self, state): ...   # 必須設定 state["last_result"] = "PASS"/"FAIL"/"TERMINATE"
    def revise_node(self, state): ...
    def summarize_node(self, state): ...

    # 選用
    def get_review_interval(self) -> int:
        return 5  # 每 5 次 PASS 後觸發一次 Loop Review
```

Plugin 在 `main.py` 啟動時透過 `discover_plugins()` 自動被載入，無需手動註冊。

---

## 7. 本地開發啟動

### 前置需求

- Docker & Docker Compose
- Python 3.11+

### 步驟

```bash
# 1. 設定環境變數
cp deploy/.env.example deploy/.env
# 編輯 deploy/.env 填入 Planka、LLM API Key、MinIO 等設定

# 2. 啟動所有服務
cd deploy
docker compose -f docker-compose.local.yml up -d

# 3. 初始化 Planka 看板
agentic-research init-planka-board

# 4. 建立研究專案
agentic-research init my-research-project

# 5. 編輯 spec，然後提交
./start.sh
```

### 環境變數說明

| 變數 | 說明 |
|---|---|
| `DATABASE_URL` | PostgreSQL 連線字串 |
| `PLANKA_API_URL` | Planka 服務網址 |
| `PLANKA_TOKEN` | Planka API Token |
| `PLANKA_BOARD_ID` | 目標看板 ID |
| `LLM_CHAIN` | LLM 提供者順序，如 `claude-cli,openai-api` |
| `ANTHROPIC_API_KEY` | Claude API 金鑰 |
| `OPENAI_API_KEY` | OpenAI API 金鑰 |
| `MINIO_ENDPOINT` | MinIO 服務位址 |

---

## 8. 常見問題排查

### Webhook 沒有被觸發

1. 確認 Planka 的 Webhook 設定指向正確的 URL（`http://<host>:7001/planka-webhook`）
2. 確認事件類型包含 `cardUpdate`
3. 查看 Framework API logs：`docker compose logs agentic-langgraph`

### 卡片停在 Spec Pending Review

- Spec 審核失敗或 LLM 無法連線
- 檢查 `LLM_CHAIN` 設定與對應 API Key
- 查看 logs 中的 `_run_spec_review_bg` 相關錯誤

### Loop Review 後無法繼續

- 確認卡片的 `description` 欄位包含 `thread_id: <project_id>`
- 確認 `projects` 資料表中有對應的 project 紀錄
- 若 graph 狀態遺失，可能需要重新啟動研究（`/start`）

### 圖狀態恢復失敗

- LangGraph 使用 PostgreSQL 儲存 checkpoint
- 確認 `DATABASE_URL` 正確且資料庫服務正常
- 檢查 LangGraph checkpoint tables 是否正常建立

---

## 相關文件

- `README.md` — 完整專案說明（繁體中文）
- `deploy/schema.sql` — 資料庫 Schema 定義
- `framework/plugin_interface.py` — Plugin API 完整說明
