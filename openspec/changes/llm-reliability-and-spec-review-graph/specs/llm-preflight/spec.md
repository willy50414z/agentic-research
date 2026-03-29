## ADDED Requirements

### Requirement: Preflight check on server startup
系統 SHALL 在 FastAPI server 的 lifespan 啟動階段執行 preflight check，驗證所有依賴服務的連通性。若任一必要服務驗證失敗，server SHALL 拒絕啟動並拋出 `RuntimeError`。

#### Scenario: All services reachable
- **WHEN** server 啟動且 LLM_CHAIN 所有 provider、Planka JWT、DB 均驗證通過
- **THEN** server 正常啟動，`app.state.preflight` 包含所有服務的 `ok: true` 結果

#### Scenario: LLM provider not logged in
- **WHEN** server 啟動且 LLM_CHAIN 包含 `claude-cli`，但 `claude auth status --json` 回傳 `loggedIn: false`
- **THEN** server 啟動失敗，log 包含明確錯誤訊息指出哪個 provider 驗證失敗

#### Scenario: Planka JWT invalid
- **WHEN** server 啟動且 `GET {PLANKA_URL}/api/v1/users/me` 回傳非 200 狀態碼
- **THEN** server 啟動失敗，log 包含 Planka 驗證失敗的訊息

#### Scenario: Database unreachable
- **WHEN** server 啟動且 `SELECT 1` 拋出例外
- **THEN** server 啟動失敗，log 包含 DB 連線失敗的訊息

### Requirement: Preflight result cache
系統 SHALL 將 preflight 結果 cache 至 `{VOLUME_BASE_DIR}/preflight_cache.json`。cache 條目 SHALL 包含 `chain_hash`（LLM_CHAIN 值的 SHA-256）、`validated_at`（ISO 8601）、各服務的 `ok`/`reason` 結果。

啟動時若 cache 存在且 `chain_hash` 相符且 `validated_at` 在 1 小時內，SHALL 跳過重新驗證直接使用 cache 結果。

#### Scenario: Cache hit within 1 hour
- **WHEN** server 啟動且 cache 存在、chain_hash 相符、validated_at 在 60 分鐘內
- **THEN** 跳過所有連通測試，使用 cache 結果，log 標示 "preflight: using cache"

#### Scenario: Cache miss — chain changed
- **WHEN** server 啟動且 LLM_CHAIN 已變更（hash 不符）
- **THEN** 重新執行全部驗證，覆寫 cache

#### Scenario: Cache expired
- **WHEN** server 啟動且 cache 存在但 validated_at 超過 60 分鐘
- **THEN** 重新執行全部驗證，覆寫 cache

### Requirement: Per-provider connectivity test method
系統 SHALL 依 provider 類型採用不同的測試方式：

- `claude-cli`：`claude auth status --json`，確認輸出包含 `"loggedIn": true`
- `gemini-cli`：`gemini --version`，returncode == 0
- `codex-cli`：`codex --version`，returncode == 0
- `opencode-cli`：`opencode --version`，returncode == 0
- `claude-api`：確認 `ANTHROPIC_API_KEY` 環境變數存在且非空
- `gemini-api`：確認 `GEMINI_API_KEY` 或 `GOOGLE_API_KEY` 存在且非空
- 未知 provider：log warning，標記為 `ok: false`，但不阻止啟動

#### Scenario: claude-cli auth check passes
- **WHEN** `claude auth status --json` 輸出包含 `"loggedIn": true`
- **THEN** claude-cli 標記為 `ok: true`

#### Scenario: Unknown provider in chain
- **WHEN** LLM_CHAIN 包含 `unknown-provider`
- **THEN** 標記為 `ok: false`，log warning，server 啟動失敗（因 unknown provider 被視為必要服務）

### Requirement: Health endpoint for preflight status
`GET /health/llm` SHALL 回傳目前 preflight 結果的 JSON，包含每個服務的 `ok` 狀態與可選的 `reason`。

#### Scenario: Health check returns preflight results
- **WHEN** `GET /health/llm` 被呼叫
- **THEN** 回傳 HTTP 200 與 JSON body `{"results": {"claude-cli": {"ok": true}, ...}, "validated_at": "..."}`

#### Scenario: Health check before preflight completes
- **WHEN** `GET /health/llm` 在 preflight 完成前被呼叫（理論上不應發生，因為 preflight 在 lifespan 中同步執行）
- **THEN** 回傳 HTTP 503 與 `{"status": "not ready"}`
