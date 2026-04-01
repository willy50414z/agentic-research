## Context

目前 `framework/llm_agent/llm_svc.py` 透過 subprocess 呼叫 Claude Code、Gemini CLI、Codex CLI 等 CLI 工具。這些工具都以 Node.js 安裝，並依賴 `~/.claude`、`~/.gemini`、`~/.codex` 內的 OAuth token 進行認證。整個 framework（包含業務邏輯、LangGraph 執行器、API server）都跑在同一個 container，任何能存取該 container 的程序也能讀取這些 credential。

本次變更目標是將 LLM 呼叫能力抽成獨立的 `llm-svc` container。CLI credential 透過 volume 掛載（只有 `llm-svc` 能讀取），API key 透過 env var 注入，主 framework container 完全不持有任何 credential。

## Goals / Non-Goals

**Goals:**
- LLM credential 只存在於 `llm-svc` container（CLI credential 透過 volume、API key 透過 env var）
- 主 framework container 透過 HTTP 呼叫 `llm-svc`，完全不需要任何 credential
- 支援全部 LLMTarget（CLI 與 API 模式）
- `run_once()` 呼叫介面保持不變（callers 不需修改）
- 本地開發（不啟動 `llm-svc` container）維持向下相容，仍走本地 CLI 模式

**Non-Goals:**
- 不支援 streaming response
- 不對 `llm-svc` API 加 auth（內部 Docker network 信任）
- 不修改 `deploy/Dockerfile`（主 framework image 清理留待後續）

## Decisions

### 1. 保留 CLI 工具，credential 透過 volume 掛載

**選擇**：`llm-svc` Dockerfile 與現有 `deploy/Dockerfile` 相同，安裝 Node.js + Claude/Gemini/Codex CLI。CLI credential（`~/.claude`、`~/.gemini`、`~/.codex`）以 read-only volume 掛載進 `llm-svc` container，主 framework container 不掛載這些 volume。

**理由**：
- Token 過期時只需在 host 重新登入，volume 自動拿到新 token，不需 rebuild image
- 保留完整 agentic CLI 能力（Claude Code 自主 file edit 等）
- 主 framework container 無任何 credential，隔離目標達成

**替代方案考慮**：Bake CLI credentials 進 image → 拒絕，token 過期需重 build，維運成本高。API-only → 失去 CLI agentic 能力，現有 use cases 需要評估。

---

### 2. `run_once()` 透明 HTTP fallthrough

**選擇**：在 `run_once()` 最前面加入：

```python
LLM_SVC_URL = os.getenv("LLM_SVC_URL")

def run_once(target, prompt, **kwargs):
    if LLM_SVC_URL:
        return _remote_invoke(target, prompt, **kwargs)
    # 原本邏輯維持不變
    ...
```

**理由**：所有 callers（`llm_providers.py`、`quant_alpha/plugin.py`）完全不需修改。本地開發不設 `LLM_SVC_URL` 時行為與現在相同。

**替代方案考慮**：在 `llm_providers.py` 加新的 HTTP provider type → 需修改 `LLM_CHAIN` 邏輯，改動面較大。

---

### 3. CLI credential via volume，API key via env var

**選擇**：兩種 credential 分開處理。

```yaml
llm-svc:
  volumes:
    - ${LOCAL_CLAUDE_CONFIG_DIR}:/root/.claude:ro
    - ${LOCAL_GEMINI_CONFIG_DIR}:/root/.gemini:ro
    - ${LOCAL_CODEX_CONFIG_DIR}:/root/.codex:ro
  environment:
    - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
    - GEMINI_API_KEY=${GEMINI_API_KEY}
    - OPENAI_API_KEY=${OPENAI_API_KEY}
```

主 framework service 不掛載這些 volume，也不傳入這些 env var。

**理由**：Image 本身不含任何 credential，可安全分享。Volume 路徑沿用 `.env` 中現有的 `LOCAL_CLAUDE_CONFIG_DIR` 等變數，不引入新的設定項目。

---

### 4. OpenCode `cwd` 跨 container 路徑對齊

**選擇**：主 framework container 與 `llm-svc` container 都掛載同一個 workspace volume，並約定使用相同的 container 內路徑（例如 `/workspace`）。caller 傳入的 `cwd` 必須是 container 內的絕對路徑。

```yaml
volumes:
  - ${WORKSPACE_DIR}:/workspace
```

**理由**：OpenCode 的 `--dir` 參數需要指向實際存在的目錄，兩邊 mount 到相同路徑確保路徑一致性。

---

### 5. httpx 作為 HTTP client

**選擇**：使用 `httpx` 套件（sync client）實作 `_remote_invoke()`。

**理由**：`run_once()` 現在是 sync，`httpx` sync client 可直接替換，不需改成 async。timeout 處理比 `requests` 精確，日後若需 async 也容易遷移。

---

### 6. /invoke endpoint 設計

**選擇**：

```
POST /invoke
Content-Type: application/json

{
  "target": "CLAUDE",            // 任何 LLMTarget 值（CLI 或 API 模式）
  "prompt": "...",
  "model": null,                  // optional
  "cwd": "/workspace/project",   // optional，OpenCode 使用
  "timeout": 10000                // ms
}

Response 200: { "output": "..." }
Response 4xx/5xx: { "detail": "..." }
```

**理由**：與現有 `run_once()` 參數完全對應，`_remote_invoke()` 直接序列化 kwargs 傳送。

## Risks / Trade-offs

| 風險 | 緩解方式 |
|------|---------|
| `llm-svc` 服務掛掉導致 framework 無法呼叫 LLM | Docker Compose 設 `restart: unless-stopped`；本地 dev 可退回 CLI 模式 |
| quota retry 邏輯（最長 24h）在 HTTP timeout 下不適合 | `_remote_invoke()` 使用 `httpx.Client(timeout=None)`，讓 `llm-svc` 內部的 retry 邏輯跑完；或日後改為 async job pattern |
| OpenCode `cwd` 路徑需要雙邊 volume 一致 | 透過 `WORKSPACE_DIR` env var 統一管理，build script 或文件說明此約定 |
| CLI token 在 `llm-svc` 重啟後需重新掛載 | volume 是 host 路徑，container 重啟不影響；host 重新登入後 token 立即生效 |

## Migration Plan

1. Build `llm-svc` image：`docker build -f deploy/llm-svc/Dockerfile -t agentic-llm-svc:latest .`
2. 更新 `.env` 加入 `LLM_SVC_URL=http://llm-svc:8001`
3. `docker compose up -d llm-svc`
4. 現有 framework container restart（讀取新的 `LLM_SVC_URL`）

**Rollback**：移除 `LLM_SVC_URL` env var，framework 自動退回本地 CLI 模式，不需要任何程式碼改動。

## Open Questions

- `llm-svc` 的 quota retry 邏輯維持現有 24h 機制，或改為指數退避？HTTP long-polling 方式要另外設計。待確認。
