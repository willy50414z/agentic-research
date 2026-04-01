## ADDED Requirements

### Requirement: POST /invoke 接收 LLM 呼叫請求
`llm-svc` 服務 SHALL 暴露 `POST /invoke` endpoint，接受 JSON body `{ target, prompt, model?, cwd?, timeout? }`，呼叫對應的 LLM（CLI 或 API 模式），並以 `{ "output": "<completion text>" }` 回應。

支援的 target 值涵蓋所有 `LLMTarget` enum：CLI 模式（`CLAUDE`、`GEMINI`、`CODEX`、`OPENCODE`、`COPILOT`）與 API 模式（`CLAUDE_API`、`GEMINI_API`、`CODEX_API`）。

#### Scenario: CLI target 呼叫成功（Claude CLI）
- **WHEN** client 發送 `POST /invoke` 且 `target = "CLAUDE"`、`~/.claude` credential volume 已掛載
- **THEN** 服務透過 subprocess 執行 claude CLI 並以 HTTP 200 回傳 `{ "output": "<completion>" }`

#### Scenario: API target 呼叫成功（Claude API）
- **WHEN** client 發送 `POST /invoke` 且 `target = "CLAUDE_API"`、`ANTHROPIC_API_KEY` env var 已設定
- **THEN** 服務呼叫 Anthropic SDK 並以 HTTP 200 回傳 `{ "output": "<completion>" }`

#### Scenario: 不支援的 target
- **WHEN** client 發送 `POST /invoke` 且 `target` 為未知字串
- **THEN** 服務回傳 HTTP 422，body 包含說明訊息

#### Scenario: CLI credential 未掛載
- **WHEN** client 發送 `POST /invoke` 且 `target = "CLAUDE"`，但 `~/.claude` 目錄不存在
- **THEN** 服務回傳 HTTP 500，body 說明 CLI credential 缺失

---

### Requirement: GET /health 健康檢查
`llm-svc` 服務 SHALL 暴露 `GET /health` endpoint，回傳服務狀態與可用 targets 清單。

#### Scenario: 服務正常運行
- **WHEN** client 發送 `GET /health`
- **THEN** 服務以 HTTP 200 回傳 `{ "status": "ok", "available_targets": ["CLAUDE", "CLAUDE_API", ...] }`，清單只包含對應 credential 或 API key 已就緒的 targets

#### Scenario: 所有 credential 皆未就緒
- **WHEN** `GET /health` 且所有 credential 和 API key 皆不存在
- **THEN** 服務仍以 HTTP 200 回應，但 `available_targets` 為空陣列

---

### Requirement: run_once() 透明 HTTP 路由
當 `LLM_SVC_URL` 環境變數已設定時，`framework/llm_agent/llm_svc.py` 的 `run_once()` SHALL 將呼叫路由到遠端 `llm-svc` HTTP API，而非本地 subprocess。當 `LLM_SVC_URL` 未設定時，行為與現有實作完全相同。

#### Scenario: LLM_SVC_URL 設定時走 HTTP
- **WHEN** `LLM_SVC_URL=http://llm-svc:8001` 且呼叫 `run_once(LLMTarget.CLAUDE, prompt)`
- **THEN** `run_once()` 向 `http://llm-svc:8001/invoke` 發送 POST 請求，回傳 response 的 `output` 欄位字串

#### Scenario: LLM_SVC_URL 未設定時走本地
- **WHEN** `LLM_SVC_URL` 未設定且呼叫 `run_once(LLMTarget.CLAUDE, prompt)`
- **THEN** `run_once()` 維持原本 CLI subprocess 邏輯，行為與現有實作相同

#### Scenario: 遠端服務不可達
- **WHEN** `LLM_SVC_URL` 設定但 `llm-svc` 服務無法連線
- **THEN** `run_once()` 拋出 `ConnectionError`，上層呼叫端可捕捉處理

---

### Requirement: llm-svc Dockerfile（CLI + API）
`deploy/llm-svc/Dockerfile` SHALL 建構包含 Python、Node.js、Claude/Gemini/Codex CLI 工具及 LLM SDK 的 image。CLI credential 不 bake 進 image，由 volume 在 runtime 掛載。

#### Scenario: Image build 成功
- **WHEN** 執行 `docker build -f deploy/llm-svc/Dockerfile`
- **THEN** build 成功，最終 image 包含 `claude`、`gemini`、`codex` 可執行檔，且不含任何 credential 檔案

#### Scenario: CLI credential 透過 volume 注入
- **WHEN** 以 `-v ~/.claude:/root/.claude:ro` 啟動 container
- **THEN** container 內 claude CLI 可正常執行，不需 rebuild image

#### Scenario: API key 透過 env var 注入
- **WHEN** 以 `-e ANTHROPIC_API_KEY=xxx` 啟動 container
- **THEN** container 內 `os.getenv("ANTHROPIC_API_KEY")` 回傳正確值，不需 rebuild image

---

### Requirement: credential 隔離
主 framework container SHALL 不掛載任何 CLI credential volume，也不接收任何 LLM API key env var。所有 LLM credential 只存在於 `llm-svc` container。

#### Scenario: 主 framework container 無 credential
- **WHEN** 主 framework container 啟動
- **THEN** container 內不存在 `~/.claude`、`~/.gemini`、`~/.codex` 目錄，且 `ANTHROPIC_API_KEY` 等 env var 未設定

#### Scenario: 主 framework 透過 HTTP 取得 LLM 能力
- **WHEN** 主 framework 呼叫 `run_once()` 且 `LLM_SVC_URL` 已設定
- **THEN** LLM 呼叫成功完成，主 framework 全程不接觸任何 credential
