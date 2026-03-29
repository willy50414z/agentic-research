## Why

`llm_svc.py` 職責混雜且含有 bug，系統啟動時不驗證 LLM 連通性導致流程中途才爆炸，spec review 背景任務沒有 checkpoint 無法 resume，且硬寫 primary/secondary 兩輪無法擴展至多個 LLM 協作審查。

## What Changes

- **移除** `llm_svc.ping()`（含 bug：enum 永遠 truthy、subprocess 字串非 list）
- **移出** Gemini `STRICT RULE` prompt prefix 從 transport 層回到呼叫端（`spec_clarifier.py`）
- **新增** `framework/llm_preflight.py`：系統啟動時驗證 LLM_CHAIN 成員、Planka JWT、DB 連線，結果 cache 至 volume；任一必要服務失敗則 server 啟動失敗
- **新增** `framework/spec_review_graph.py`：將 spec review 工作流從純 Python 背景任務改為 LangGraph StateGraph，支援 PostgresSaver checkpoint 與 error resume
- **修改** `framework/spec_clarifier.py`：新增 `initial`、`review`、`synthesize` 三個 prompt role，取代原本的 `primary`/`secondary`
- **修改** `framework/api/server.py`：lifespan 加入 preflight check；`_run_spec_review_bg` 改為呼叫 spec review graph；新增 `GET /health/llm` endpoint

## Capabilities

### New Capabilities

- `llm-preflight`：系統啟動時對 LLM_CHAIN 所有成員執行連通測試，同時驗證 Planka JWT 與 DB，結果 cache 避免重複驗證，失敗則阻止 server 啟動
- `spec-review-graph`：以 LangGraph StateGraph 執行多 LLM 協作 spec review，採 Author → Reviewers → Synthesizer 迴圈設計，每輪 checkpoint 支援 error resume，輪數由 LLM_CHAIN 長度決定

### Modified Capabilities

- `api-server`：lifespan 新增 preflight 呼叫；spec review 觸發路徑改為 graph invocation；新增 `/health/llm` endpoint

## Impact

- `framework/llm_agent/llm_svc.py`：移除 `ping()`，移出 Gemini prompt prefix
- `framework/llm_providers.py`：移除對 `ping` 的 import（若有直接使用）
- `framework/spec_clarifier.py`：新增 role，Gemini prefix 移入此處
- `framework/api/server.py`：lifespan、webhook handler、新 endpoint
- 新增檔案：`framework/llm_preflight.py`、`framework/spec_review_graph.py`
- 新增 prompt 模板：`framework/prompts/spec_agent_initial.txt`、`spec_agent_review.txt`、`spec_agent_synthesize.txt`
- 依賴不變：LangGraph、PostgresSaver、FastAPI、httpx 均已存在
