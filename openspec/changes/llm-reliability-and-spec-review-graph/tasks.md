## 1. llm_svc.py 清理

- [x] 1.1 移除 `ping()` 函式及其所有 import（`framework/llm_svc.py`、`framework/llm_providers.py`）
- [x] 1.2 將 Gemini `STRICT RULE` prompt prefix 從 `run_once()` 移出，移入 `spec_clarifier.py` 的 `run_spec_agent()` 中依 provider name 條件注入
- [x] 1.3 確認 `llm_svc.py` 移除後無 import 錯誤（執行 `python -c "from framework.llm_agent.llm_svc import run_once"`）

## 2. framework/llm_preflight.py 新增

- [x] 2.1 實作 `_check_claude_cli()`：執行 `claude auth status --json`，確認輸出含 `"loggedIn": true`
- [x] 2.2 實作 `_check_gemini_cli()`、`_check_codex_cli()`、`_check_opencode_cli()`：各執行 `{tool} --version`，returncode == 0
- [x] 2.3 實作 `_check_api_provider()`：確認對應環境變數（`ANTHROPIC_API_KEY` 等）存在且非空
- [x] 2.4 實作 `_check_planka()`：呼叫 `GET {PLANKA_URL}/api/v1/users/me` with `Authorization: Bearer {PLANKA_TOKEN}`，確認 HTTP 200
- [x] 2.5 實作 `_check_database()`：執行 `SELECT 1` via psycopg3，確認無例外
- [x] 2.6 實作 `preflight_check(db_url, planka_url, planka_token, llm_chain_str)`：組合所有檢查，讀寫 `{VOLUME_BASE_DIR}/preflight_cache.json`（hash 比對 + 1小時 TTL），任一失敗則 raise `RuntimeError`
- [x] 2.7 實作 `get_preflight_results()`：回傳最近一次 preflight 結果供 `/health/llm` endpoint 使用

## 3. framework/api/server.py 修改

- [x] 3.1 在 `_lifespan` 中加入 `preflight_check(...)` 呼叫（yield 之前），傳入 `DATABASE_URL`、`PLANKA_URL`、`PLANKA_TOKEN`、`LLM_CHAIN`
- [x] 3.2 將 preflight 結果存入 `app.state.preflight`
- [x] 3.3 新增 `GET /health/llm` endpoint，呼叫 `get_preflight_results()` 並回傳 JSON
- [x] 3.4 將 `_run_spec_review_bg` 的觸發邏輯替換為呼叫 `spec_review_graph`（在 background task 中 invoke）
- [x] 3.5 移除 `_build_llm_chain()` 在 `_run_spec_review_bg` 內的即時呼叫（改由 spec_review_graph init 節點處理）

## 4. framework/spec_clarifier.py 修改

- [x] 4.1 新增 `initial`、`review`、`synthesize` 三個 prompt role 至 `_load_prompt()`，各對應 `spec_agent_initial.txt`、`spec_agent_review.txt`、`spec_agent_synthesize.txt`
- [x] 4.2 將既有 `primary` prompt 模板重新命名（或 alias）為 `initial`
- [x] 4.3 將 Gemini `STRICT RULE` prefix 邏輯移入 `run_spec_agent()`，依 `provider_name` 條件注入（`if "gemini" in provider_name`）
- [x] 4.4 修改 `run_spec_agent()` 簽名加入 `round_index: int = 0`，供 `review` role 決定輸出檔名 `review_notes_round{N}.txt`
- [x] 4.5 `review` role：讀取 `current_spec_md`，輸出只寫 `review_notes_round{N}.txt`，不產出 `reviewed_spec_*.md`
- [x] 4.6 `synthesize` role：prompt 中注入所有 `review_notes_round*.txt` 的內容，輸出 `reviewed_spec_final.md` + status file

## 5. prompt 模板新增

- [x] 5.1 建立 `framework/prompts/spec_agent_initial.txt`（基於現有 `spec_agent_primary.txt`，調整角色說明）
- [x] 5.2 建立 `framework/prompts/spec_agent_review.txt`（reviewer 角色：列出問題與建議，不改稿，每條意見不超過 3 句）
- [x] 5.3 建立 `framework/prompts/spec_agent_synthesize.txt`（synthesizer 角色：整合所有 reviewer 意見，產出最終 spec + status file）

## 6. framework/spec_review_graph.py 新增

- [x] 6.1 定義 `SpecReviewState` TypedDict（`project_id`, `card_id`, `spec_path`, `participants`, `current_round`, `total_rounds`, `current_spec_md`, `review_notes`, `status`, `questions`）
- [x] 6.2 實作 `spec_review_init` 節點：讀取 spec 檔案，設定 `participants`（從 `LLM_CHAIN` 拆分）、`total_rounds`、`current_spec_md`、`current_round=0`
- [x] 6.3 實作 `spec_review_round` 節點：依 `current_round` 決定角色與 LLM，呼叫 `run_spec_agent()`，更新 `current_spec_md`（author/synthesizer）或 `review_notes`（reviewer），遞增 `current_round`
- [x] 6.4 實作 `_route_review()` conditional edge function：`current_round < total_rounds - 1` → `"spec_review_round"`，否則 → `"spec_finalize"`
- [x] 6.5 實作 `spec_finalize` 節點：依 `status` 執行 pass/need_update/abort 各自路徑（呼叫 `create_project`、移 Planka 卡片、post comment）
- [x] 6.6 建構 `StateGraph`，連接 `spec_review_init → spec_review_round → [route] → spec_finalize`，套用 PostgresSaver checkpointer
- [x] 6.7 實作 `get_or_build_spec_review_graph(config)` 函式（類比現有 `get_or_build_graph`），供 `server.py` 呼叫

## 7. 測試與驗證

- [ ] 7.1 手動測試：單一 provider chain（`LLM_CHAIN="claude-cli"`），驗證 2 輪流程正常完成
- [ ] 7.2 手動測試：雙 provider chain（`LLM_CHAIN="claude-cli,gemini-cli"`），驗證 3 輪流程，review_notes 正確傳入 synthesizer
- [ ] 7.3 手動測試 error resume：在 round 1 人為製造失敗（例如暫時將 gemini 設為不可用），確認 resume 後從 round 1 重跑
- [ ] 7.4 手動測試 preflight：關閉 claude 登入狀態，確認 server 啟動失敗且 log 訊息明確
- [ ] 7.5 手動測試 `/health/llm`：server 啟動後呼叫 endpoint，確認回傳正確的 preflight 結果
- [ ] 7.6 驗證 cache：server 重啟時（chain 未變、1小時內），確認 log 顯示 "preflight: using cache"
