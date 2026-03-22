# RD 部屬指南 — Agentic Research

## 前置需求

| 工具 | 版本需求 | 備註 |
|------|----------|------|
| Docker Engine | 24+ | |
| Docker Compose | v2.20+ | 使用 `docker compose`（非 `docker-compose`） |
| Git | 任意 | |
| LLM API Key | — | Anthropic / OpenAI / Gemini 至少一個 |

---

## 1. Clone & 初始化

```bash
git clone <repo-url> agentic-research
cd agentic-research
cp deploy/.env.example .env
```

---

## 2. 設定 `.env`

以下是完整的 `.env` 範本，依序說明每個變數的用途與填寫方式。

```dotenv
# ── 磁碟掛載路徑 ──────────────────────────────────────────────────────────────
# Docker volume 的 host 端根目錄（postgres / minio / mlflow 資料都存在這裡）
# Linux/macOS 範例: /opt/agentic-research/data
# Windows 範例:     E:/docker_data/agentic-research
VOLUME_BASE_DIR=/opt/agentic-research/data

# docker-compose 檔案路徑（一般不需要改）
COMPOSE_FILE=docker/docker-compose.yml

# ── PostgreSQL ────────────────────────────────────────────────────────────────
POSTGRES_USER=postgres
POSTGRES_PASSWORD=postgres                  # 生產環境請換成強密碼
POSTGRES_DB=agentic-research
POSTGRES_HOST_AUTH_METHOD=trust             # 如需 md5 驗證可改為 md5

# LangGraph checkpointer 與 business schema 使用的連線字串
# 注意：在 container 內，host 要寫 "postgres"（service 名稱）
#       在 host 機器直連時，host 寫 "localhost"
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/agentic-research

# ── Artifacts ─────────────────────────────────────────────────────────────────
# container 內的 artifact 存放路徑（一般不需要改）
ARTIFACTS_DIR=/app/artifacts

# ── MLflow（可選）─────────────────────────────────────────────────────────────
# container 內 mlflow 服務名稱為 "mlflow"
# 如果不需要實驗追蹤，可以留空或移除此行
MLFLOW_TRACKING_URI=http://mlflow:5000

# ── Planka（HITL 看板）────────────────────────────────────────────────────────
# Planka 的對外 URL（瀏覽器存取用）
BASE_URL=http://localhost:7002

# JWT secret，務必換成隨機長字串
# 產生方式：openssl rand -hex 32
SECRET_KEY=replace_this_with_a_long_random_secret_key

# Planka 預設管理員帳號（首次啟動後建議從 UI 修改密碼）
DEFAULT_ADMIN_EMAIL=admin@example.com
DEFAULT_ADMIN_PASSWORD=adminpassword
DEFAULT_ADMIN_NAME=Admin
DEFAULT_ADMIN_USERNAME=admin

# Framework 呼叫 Planka API 時使用的 URL（container 內改用 service 名稱）
PLANKA_API_URL=http://planka:1337

# Planka JWT token（啟動後從 UI 取得 → 帳號設定 → Access Token）
PLANKA_TOKEN=<your-planka-token>

# Planka Board ID（從 URL 取得：http://localhost:7002/board/<BOARD_ID>）
PLANKA_BOARD_ID=<your-board-id>

# ── MinIO（S3 相容物件存儲）───────────────────────────────────────────────────
MINIO_ROOT_USER=minioadmin
MINIO_ROOT_PASSWORD=minioadmin              # 生產環境請換成強密碼
MINIO_ENDPOINT=http://minio:9000            # container 內部使用
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_SECURE=false
MINIO_ARTIFACTS_BUCKET=research-artifacts

# ── LLM 整合 ─────────────────────────────────────────────────────────────────
# 指定 LLM provider 順序（逗號分隔，依序 fallback）
# 支援的值：claude, openai, gemini, local
LLM_CHAIN=claude,openai

# 至少填一個 API Key
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GEMINI_API_KEY=...
GOOGLE_API_KEY=...                          # Gemini 的替代 key

# 本地 LLM（Ollama）設定（若 LLM_CHAIN 含 local）
LOCAL_LLM_ENDPOINT=http://localhost:11434
LOCAL_LLM_MODEL=llama3.2
```

### 重要設定說明

| 變數 | 說明 |
|------|------|
| `VOLUME_BASE_DIR` | **必填**。所有 Docker volume 的 host 端根目錄，確保磁碟有足夠空間 |
| `SECRET_KEY` | **必改**。Planka JWT secret，使用 `openssl rand -hex 32` 產生 |
| `DATABASE_URL` | container 內部請用 `@postgres:5432`；host 機器直連用 `@localhost:5432` |
| `PLANKA_API_URL` | container 內部請用 `http://planka:1337`；從 host 呼叫用 `http://localhost:7002` |
| `LLM_CHAIN` | 決定 AI 使用哪個 LLM，至少要有一個對應的 API Key |
| `PLANKA_TOKEN` | 需要先啟動 Planka 後，從 UI 取得 token 再填入（見步驟 5）|

---

## 3. 啟動 Docker 服務

```bash
# 從 repo 根目錄執行
docker compose -f deploy/docker-compose.yml up -d

# 確認服務狀態
docker compose -f deploy/docker-compose.yml ps
```

預期輸出（所有服務都應為 `healthy` 或 `running`）：

```
NAME                      STATUS
agentic-minio             running (healthy)
agentic-minio-init        exited (0)          ← 初始化完成後正常退出
agentic-mlflow            running
agentic-research-postgres running (healthy)
agentic-langgraph         running
planka                    running
```

### 各服務埠號

| 服務 | Host 埠 | 用途 |
|------|---------|------|
| langgraph-engine | `7001` | FastAPI（Webhook 接收端） |
| planka | `7002` | Kanban UI |
| postgres | `5432` | PostgreSQL（可選：直連 debug 用） |
| minio | `9000` | MinIO S3 API |
| minio console | `9001` | MinIO Web UI |
| mlflow | `5000` | MLflow 實驗追蹤 UI |

---

## 4. 資料庫 Migration

LangGraph 的 checkpoint table 會在服務啟動時**自動建立**。

Business schema（projects / loop_metrics / checkpoint_decisions）需要手動執行 migration：

```bash
# 方法一：進入 postgres container 執行
docker exec -i agentic-research-postgres psql \
  -U postgres -d agentic-research \
  < deploy/schema.sql

# 方法二：透過 psql 直連（需要 host 機器安裝 psql）
psql postgresql://postgres:postgres@localhost:5432/agentic-research \
  -f deploy/schema.sql
```

---

## 5. 初始化 Planka

### 5.1 登入 Planka

瀏覽器開啟 `http://localhost:7002`，使用 `.env` 中設定的管理員帳號登入。

### 5.2 建立 Board 並取得 Board ID

1. 建立一個新的 Board（名稱建議：`Agentic Research`）
2. 從 URL 複製 Board ID：`http://localhost:7002/board/<BOARD_ID>`
3. 填入 `.env` 的 `PLANKA_BOARD_ID`

### 5.3 取得 Planka Access Token

1. 點擊右上角個人頭像 → **Account Settings**
2. 找到 **Access Token** 區塊，點擊 **Generate Token**
3. 複製 token 填入 `.env` 的 `PLANKA_TOKEN`

### 5.4 設定 Planka Webhook

在 Planka Board 設定中，新增 Webhook URL：
```
http://localhost:7001/planka-webhook
```
> 如果 Planka 和 langgraph-engine 在同一個 Docker network 內，改用：
> `http://agentic-langgraph:8000/planka-webhook`

### 5.5 重啟 langgraph-engine 套用設定

```bash
docker compose -f deploy/docker-compose.yml restart langgraph-engine
```

服務啟動時會自動在 Planka Board 建立 6 個欄位：
`Planning → Spec Pending Review → Verify → Review → Done → Failed`

---

## 6. 確認服務正常

```bash
# 健康檢查
curl http://localhost:7001/health
# 預期回應: {"status": "ok"}

# 查看 langgraph-engine logs
docker logs agentic-langgraph --tail 50

# 查看 postgres 中的 table
docker exec agentic-research-postgres psql -U postgres -d agentic-research -c "\dt"
```

---

## 7. 服務管理

```bash
# 停止所有服務（保留資料）
docker compose -f deploy/docker-compose.yml down

# 停止並清除所有資料（含 volume）
docker compose -f deploy/docker-compose.yml down -v

# 重新 build langgraph-engine image
docker compose -f deploy/docker-compose.yml build langgraph-engine

# 查看即時 log
docker compose -f deploy/docker-compose.yml logs -f langgraph-engine

# 重啟單一服務
docker compose -f deploy/docker-compose.yml restart langgraph-engine
```

---

## 8. 常見問題排除

### PostgreSQL healthcheck 一直失敗
```bash
# 確認 POSTGRES_USER 和 POSTGRES_DB 設定正確
docker logs agentic-research-postgres
```

### langgraph-engine 啟動失敗
```bash
docker logs agentic-langgraph --tail 100
# 常見原因：DATABASE_URL 寫了 localhost（在 container 內應寫 postgres）
#           LLM API Key 未設定
```

### Planka Webhook 沒有觸發
1. 確認 Webhook URL 設定正確（container 內部用 service name）
2. 確認 `PLANKA_TOKEN` 未過期
3. 查看 langgraph-engine log 確認有無收到請求

### MinIO bucket 沒有建立
```bash
# minio-init 是 one-shot container，確認它有正常退出（exit 0）
docker logs agentic-minio-init
```

### Planka 附件上傳失敗
- 確認 MinIO `planka-attachments` bucket 存在
- 確認 Planka 的 `S3_ENDPOINT` 可以從 planka container 連通 minio

---

## 9. 生產環境注意事項

- [ ] `SECRET_KEY` 換成 32 bytes 以上的隨機字串
- [ ] `POSTGRES_PASSWORD` 換成強密碼，`POSTGRES_HOST_AUTH_METHOD` 改為 `md5`
- [ ] `MINIO_ROOT_PASSWORD` 換成強密碼
- [ ] `DEFAULT_ADMIN_PASSWORD` 登入後立刻修改
- [ ] 確認 `VOLUME_BASE_DIR` 有足夠磁碟空間並做備份規劃
- [ ] LLM API Key 建議透過 secret manager 注入，不要直接寫在 `.env` 並 commit
- [ ] 考慮使用 nginx reverse proxy 加上 TLS，不要直接暴露服務埠號
