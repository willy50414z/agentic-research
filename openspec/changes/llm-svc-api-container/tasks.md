## 1. 準備 llm-svc 目錄結構

- [ ] 1.1 建立 `deploy/llm-svc/` 目錄
- [ ] 1.2 新增 `httpx` 至 `requirements.txt`

## 2. 建立 llm-svc Dockerfile

- [ ] 2.1 撰寫 `deploy/llm-svc/Dockerfile`：以 python:3.14-slim 為 base，安裝 Node.js、`@anthropic-ai/claude-code`、`@google/gemini-cli`、`@openai/codex` CLI 工具，以及 `anthropic`、`openai`、`google-generativeai`、`fastapi`、`uvicorn`、`httpx` Python 套件
- [ ] 2.2 確認 `docker build -f deploy/llm-svc/Dockerfile` 成功，image 包含 `claude`、`gemini`、`codex` 可執行檔，不含任何 credential 檔案

## 3. 實作 llm-svc FastAPI app

- [ ] 3.1 撰寫 `deploy/llm-svc/main.py`：定義 `InvokeRequest` Pydantic model（target, prompt, model, cwd, timeout）
- [ ] 3.2 複製 `framework/llm_agent/llm_svc.py` 與 `framework/llm_agent/llm_target.py` 進 llm-svc image，或在 Dockerfile 中 COPY 整個 `framework/llm_agent/`
- [ ] 3.3 實作 `POST /invoke`：將 request 轉為 `run_once()` 呼叫，回傳 `{ "output": "..." }`
- [ ] 3.4 實作 `GET /health`：偵測哪些 CLI credential 目錄存在、哪些 API key env var 已設定，回傳 `{ "status": "ok", "available_targets": [...] }`
- [ ] 3.5 處理 unsupported target（HTTP 422）、missing credential（HTTP 500）錯誤回應

## 4. 修改 run_once() 加入 HTTP fallthrough

- [ ] 4.1 在 `framework/llm_agent/llm_svc.py` 頂層讀取 `LLM_SVC_URL = os.getenv("LLM_SVC_URL")`
- [ ] 4.2 實作 `_remote_invoke(target, prompt, **kwargs) -> str`：使用 `httpx.Client(timeout=None)` 呼叫 `{LLM_SVC_URL}/invoke`，回傳 response `output` 欄位
- [ ] 4.3 在 `run_once()` 最前面加入 `if LLM_SVC_URL: return _remote_invoke(...)` 分支
- [ ] 4.4 處理連線失敗，拋出帶有明確訊息的 `ConnectionError`

## 5. 更新 docker-compose.yml

- [ ] 5.1 新增 `llm-svc` service：image `agentic-llm-svc:latest`、port `8001:8001`、`restart: unless-stopped`
- [ ] 5.2 為 `llm-svc` 掛載 CLI credential volumes（read-only）：`${LOCAL_CLAUDE_CONFIG_DIR}:/root/.claude:ro`、`${LOCAL_GEMINI_CONFIG_DIR}:/root/.gemini:ro`、`${LOCAL_CODEX_CONFIG_DIR}:/root/.codex:ro`
- [ ] 5.3 為 `llm-svc` 注入 API key env vars：`ANTHROPIC_API_KEY`、`GEMINI_API_KEY`、`OPENAI_API_KEY`
- [ ] 5.4 為 `llm-svc` 與主 framework service 掛載相同的 workspace volume（`${WORKSPACE_DIR}:/workspace`），確保 OpenCode `cwd` 路徑一致
- [ ] 5.5 在主 framework service 加入 `LLM_SVC_URL=http://llm-svc:8001` 環境變數，並移除其 credential volume 與 API key env vars

## 6. 更新設定檔

- [ ] 6.1 在 `.env` 新增 `LLM_SVC_URL`（本地開發留空，container 環境設為 `http://llm-svc:8001`）
- [ ] 6.2 在 `.env` 新增 `WORKSPACE_DIR`（host 上的 workspace 路徑，對應 container 內 `/workspace`）

## 7. 驗證

- [ ] 7.1 `docker compose up llm-svc` 後，`GET /health` 回傳正確 `available_targets`（CLI + API）
- [ ] 7.2 設定 `LLM_SVC_URL`，從主 framework 呼叫 `run_once(LLMTarget.CLAUDE, "ping")` 成功取得回應（走 CLI 路徑）
- [ ] 7.3 設定 `LLM_SVC_URL`，從主 framework 呼叫 `run_once(LLMTarget.CLAUDE_API, "ping")` 成功取得回應（走 API 路徑）
- [ ] 7.4 移除 `LLM_SVC_URL`，確認 `run_once()` 退回本地 CLI 模式，行為不變
- [ ] 7.5 確認主 framework container 內不存在 `~/.claude` 等 credential 目錄
