## Why

現有架構中，LLM credential（CLI OAuth token 與 API key）與主 framework 程式碼共存於同一 container，任何能存取該 container 的程序都能讀取這些 credential。將 `llm_svc` 抽成獨立的 container，credential 只在 `llm-svc` container 中存在（CLI credential 透過 volume 掛載、API key 透過 env var 注入），主 framework 只透過 HTTP 呼叫取得 LLM 能力，不直接持有任何 credential。

## What Changes

- **新增** `llm-svc` 獨立 Docker service，暴露 `POST /invoke` 與 `GET /health` HTTP API
- **新增** `deploy/llm-svc/Dockerfile`：包含 Python + Node.js + Claude/Gemini/Codex CLI 工具 + LLM SDK，與現有 `deploy/Dockerfile` 安裝步驟相同，但作為獨立 service 運行
- **新增** `deploy/llm-svc/main.py`：FastAPI app，內部呼叫現有 `llm_svc.py` 完整邏輯（CLI + API targets 全部支援）
- **修改** `framework/llm_agent/llm_svc.py`：`run_once()` 偵測 `LLM_SVC_URL` env var，若設定則透過 HTTP 呼叫遠端服務，否則維持原本本地行為（向下相容）
- **修改** `docker-compose.yml`：新增 `llm-svc` service，掛載 credential volume，主 framework service 加入 `LLM_SVC_URL` 環境變數
- **修改** `.env`：新增 `LLM_SVC_URL`、CLI credential volume 路徑設定

## Capabilities

### New Capabilities

- `llm-svc`: 獨立 HTTP 服務，接收 `{ target, prompt, model, cwd, timeout }` 請求，支援全部 LLMTarget（CLI 模式：CLAUDE、GEMINI、CODEX、OPENCODE；API 模式：CLAUDE_API、GEMINI_API、CODEX_API），回傳 LLM completion 文字

### Modified Capabilities

（無現有 spec 需要更新）

## Impact

- **framework/llm_agent/llm_svc.py**：加入 HTTP client fallthrough，本地 dev 不受影響
- **framework/llm_providers.py**：不需修改（透過 run_once() 透明銜接）
- **projects/quant_alpha/plugin.py**：不需修改
- **docker-compose.yml**：新增一個 service，掛載 CLI credential volume，注入 API key env vars
- **新增依賴**：`httpx`（HTTP client，sync，用於 _remote_invoke()）
