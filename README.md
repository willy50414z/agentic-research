# Agentic Research

一個由 **Planka 看板事件驅動** 的自動化量化策略研究框架。使用者在 Planka 上拖動卡片即觸發對應流程：LLM 審查策略 spec、規劃實作、跑 Freqtrade 回測、分析績效、迭代修正，並把每一輪產出（plan / strategy / backtest / report）回傳成卡片附件。

> 本份 README 描述系統的整體流程與每個流程的程式入口位置，方便新加入的開發者快速定位程式碼。
> 進階操作流程請參考 `docs/AGENTIC_RESEARCH_SOP_ZH.md`。

---

## 1. 系統架構總覽

```
┌──────────┐  cardUpdate webhook   ┌─────────────────────┐
│  Planka  │ ────────────────────▶ │  FastAPI            │
│  (UI)    │ ◀──── PATCH card ──── │  agentic-framework  │
└──────────┘                       │  app/main.py        │
                                   └──────────┬──────────┘
                                              │
                ┌─────────────────────────────┼─────────────────────────────┐
                ▼                             ▼                             ▼
        Spec Review Step               Executing Step                   LLM 呼叫
   app/workflow/                  app/workflow/                    llm-svc (8001)
     spec_review_step.py            executing_step.py             claude / gemini / codex
                                              │
                                              ▼
                                  Freqtrade Steps Pipeline
                                  app/freqtrade/steps/*
                                  plan → implement → test
                                  → analyze → revise/summarize
                                              │
                                              ▼
                                  Freqtrade backtest CLI
                                  (subprocess)
```

依賴的外部服務（均由 `deploy/docker-compose.yml` 啟動）：

| 服務 | 用途 | Port |
|------|------|------|
| `postgres` | 專案狀態、workflow_step、loop_metrics、checkpoint_decisions | 5432 |
| `planka` | HITL 看板 UI（人類審查、拖卡觸發 webhook） | 7002 |
| `minio` | Planka 附件 + research artifacts 的 S3 相容儲存 | 9000 / 9001 |
| `mlflow` | 回測指標追蹤（可選） | 5000 |
| `llm-svc` | 統一封裝 Claude / Gemini / Codex CLI，主框架不持有 credential | 8001 |
| `agentic-framework-api` | FastAPI 主程式 | 7001 |

---

## 2. 整體流程

研究專案依 Planka 看板欄位推進，狀態機如下：

```
Planning ─▶ Spec Pending Review ─▶ Executing ─▶ Review ─▶ Done
                                       │           │
                                       └──────────▶ Failed
```

每一次「卡片被拖到新欄位」都會觸發 `POST /planka-webhook`，server 依目的欄位分派到不同的 workflow：

| 拖入欄位 | 觸發處理 | 程式入口 |
|----------|----------|----------|
| `Spec Pending Review` | LLM 審查 `spec.md`（2 輪：initial / synthesize） | `app/workflow/spec_review_step.py::run_spec_review_step` |
| `Executing` | 進入 plan → implement → test → analyze → … 迴圈 | `app/workflow/executing_step.py::dispatch_step` |
| 其他 | 忽略 | — |

---

## 3. 各流程與程式入口

### 3.1 應用啟動

| 階段 | 程式入口 | 說明 |
|------|----------|------|
| 容器入口 | `deploy/Dockerfile` | `uvicorn main:app --host 0.0.0.0 --port 8000` |
| FastAPI 應用 | `app/main.py` | 載入 `.env`、設定 logging、再 import `app.api.server.app` |
| FastAPI 路由 + Lifespan | `app/api/server.py` | 註冊 webhook / health 路由、跑 LLM/DB preflight、啟動 stale review scheduler |
| LLM target 解析 | `app/llm.py` | 依 `LLM_<STAGE>` env var 解析使用哪一個 CLI（claude/gemini/codex） |
| DB schema | `app/db/schema.sql` | `projects` / `loop_metrics` / `checkpoint_decisions` 三表 |
| Planka API client | `app/clients/task_board.py` | 卡片移動、附件上傳/下載、留言、custom field 管理 |

啟動時 `_run_preflight()` 會檢查所有設定的 LLM target 與 DB 是否可用，任一失敗就拒絕啟動。

### 3.2 Webhook 接收

| 入口 | 檔案 | 說明 |
|------|------|------|
| `POST /planka-webhook` | `app/api/server.py::planka_webhook` | 解析卡片事件，取出 `list_name` 與 `project_id`，丟到 `BackgroundTasks` |
| `POST /init-planka-board` | `app/api/server.py::init_planka_board` | 一次性建立 Planka project / board / lists / custom fields / webhook |
| `GET /health`、`GET /health/llm` | `app/api/server.py` | 健康檢查 |
| Stale review 清理 | `app/api/server.py::AppServices.scan_stalled_reviews` | 每 60 秒清理 `review_in_progress` 超時的卡片 |

### 3.3 Spec Review 階段（卡片拖入 `Spec Pending Review`）

| 子步驟 | 程式入口 | 產出 |
|--------|----------|------|
| 入口 | `app/workflow/spec_review_step.py::run_spec_review_step` | — |
| 主類別 | `SpecReviewRunner.run` | 下載卡片附件中的 `spec.md`、判斷 step、跑 initial / synthesize |
| Initial 輪 | `SpecReviewRunner._run_initial` | `reviewed_spec_initial.md` 或 `questions.txt` |
| Synthesize 輪 | `SpecReviewRunner._run_synthesize` | `reviewed_spec_final.md` + `spec_fields.json` |
| Prompt 模板 | `app/prompts/spec_review/spec_agent_*.txt` | initial / refine / synthesize |
| Spec 審查規則 | `.ai/rules/spec-review.md` | PASS / NEED_UPDATE 判定門檻 |

通過後寫入 `projects.config.spec`、設 `workflow_step = plan`、卡片移到 `Executing`，由 Webhook 再次觸發 dispatch_step 開始研究循環。

### 3.4 研究循環（卡片拖入 `Executing`）

主入口：`app/workflow/executing_step.py::dispatch_step`。會在 while 迴圈裡讀取 `projects.workflow_step` 並分派到對應 handler，直到進入 terminal step、HITL pause 或例外。

| `workflow_step` | Handler | Step 實作 |
|-----------------|---------|-----------|
| `plan` | `_run_plan` | `app/freqtrade/steps/plan.py::plan_step` |
| `implement` | `_run_implement` | `app/freqtrade/steps/implement.py::implement_step` |
| `test` | `_run_test` | `app/freqtrade/steps/test.py::test_step` |
| `analyze` | `_run_analyze` | `app/freqtrade/steps/analyze.py::analyze_step` |
| `revise` | `_run_revise` | `app/freqtrade/steps/revise/v2.py::revise_step`（依 `REVISE_PIPELINE_VERSION` 切 v1 / v2） |
| `summarize` | `_run_summarize` | `app/freqtrade/steps/summarize.py::summarize_step` |
| `terminate` | `_run_terminate_summarize` | `app/freqtrade/steps/terminate.py::terminate_summarize_step` |
| `done` | — | 終態，dispatch 結束 |

LLM 呼叫共用入口：`app/freqtrade/steps/_common.py::_call_llm`（包裝 `llm_eval.llm_svc.run_with_fallback`）。
所有 prompt 模板放在 `app/prompts/freqtrade/*.txt`。
產出檔（plan_output.json、strategy.py、backtest zip、summary report …）統一寫到 `ARTIFACTS_DIR`，由 `_upload_*` 函式上傳成 Planka 卡片附件。

#### Freqtrade 回測子流程

| 子步驟 | 程式入口 | 說明 |
|--------|----------|------|
| 回測編排 | `app/freqtrade/backtest.py::run_backtest_is_oos` | 依 spec 拆 IS / OOS，呼叫下列模組 |
| Config 生成 | `app/freqtrade/config_generator.py::generate_config` | 寫 freqtrade config.json |
| 資料下載 + CLI 呼叫 | `app/freqtrade/runner.py` | `freqtrade download-data` / `backtesting` |
| Strategy snapshot（v2 revise） | `app/freqtrade/steps/strategy_snapshot.py` | 把上一輪 .py 寫到 `artifacts/strategies/v{N}/` |
| Strategy AST 萃取 | `app/freqtrade/strategy_extractor.py` | 給 revise pipeline 比對改了什麼 |
| Checklist 協定 | `app/freqtrade/checklist.py` | v2 revise pipeline 的 checklist schema |
| Audit（v2 Stage E） | `app/freqtrade/audit.py` | AST 檢查 + 全域 invariant + LLM3 logic audit |
| 結果解析 | `app/freqtrade/result_parser.py` | 解 `*.zip` → `is_metrics` / `oos_metrics` |

### 3.5 結案

- 通過所有迴圈：`_run_summarize` 產生 `vN_researchsummary_*.md`、上傳到卡片，把 `workflow_step` 設為 `done`、卡片移到 `Done`。
- 用盡 `max_loops` 或 LLM 主動 TERMINATE：`_run_terminate_summarize` 產出 `v0_*_summary_report.md`，卡片移到 `Review` 等待人類最終評查。
- 任一 step 例外：dispatch_step 補 `write_error_report`（`app/workflow/error_report.py`）、卡片移到 `Failed`，原因留言在卡片上。

---

## 4. 重要環境變數

設定檔範例：`deploy/.env`。最常用的幾個：

| 變數 | 說明 |
|------|------|
| `DATABASE_URL` | Postgres 連線字串 |
| `PLANKA_API_URL` / `PLANKA_TOKEN` / `PLANKA_BOARD_ID` | Planka API 認證；`init-planka-board` 會回傳前兩者 |
| `LLM_DEFAULT` | 預設 LLM target（`claude` / `gemini` / `codex`），可填多個用 `,` 串接表示 fallback |
| `LLM_<STAGE>` | 覆寫特定階段的 LLM target，例如 `LLM_SPEC_REVIEW_INITIAL`、`LLM_FREQTRADE_STEPS`、`LLM_REVISE_LLM1` |
| `LLM_SVC_URL` | 在容器內呼叫 llm-svc 的位址（預設 `http://llm-svc:8001`） |
| `BACKTEST_MODE` | `mock`（不跑真的 freqtrade）或 `real` |
| `REVISE_PIPELINE_VERSION` | `v1`（legacy）或 `v2`（checklist + audit），首次進 revise 時鎖入 project config |
| `MAX_LOOPS` / Planka 卡片上的 `max_loops` 自訂欄位 | 控制 analyze FAIL 後最多再 revise 幾次 |
| `ARTIFACTS_DIR` | 各 step 產出檔的根目錄（容器內預設 `/app/artifacts`） |

---

## 5. 開發者快速上手

```bash
# 1. 啟動所有依賴服務
docker compose -f deploy/docker-compose.local.yml up -d

# 2. 初始化 Planka board（第一次使用）
curl -X POST http://localhost:7001/init-planka-board \
  -H "Content-Type: application/json" \
  -d '{"base_url":"http://localhost:7002","email":"agentic@local.dev","password":"agentic-planka-pwd"}'
# 將回傳的 token / board_id 寫回 deploy/.env，重啟 agentic-framework-api

# 3. 健康檢查
curl http://localhost:7001/health
curl http://localhost:7001/health/llm

# 4. 上傳 spec.md 到一張新卡片 → 拖到 "Spec Pending Review" → 等 LLM 審查
#    通過後系統會把卡片自動推進到 "Executing"，開始研究循環
```

跑單元測試：

```bash
pytest tests/
```

---

## 6. 文件索引

| 主題 | 路徑 |
|------|------|
| 完整 SOP（操作 + 異常排查） | `docs/AGENTIC_RESEARCH_SOP_ZH.md` |
| Plugin 介面規格 | `docs/PLUGIN_SPEC.md` |
| Research workflow 設計 | `docs/RESEARCH_WORKFLOW_DESIGN.md` |
| Spec 審查規則 | `.ai/rules/spec-review.md` |
| 共用 rules / skills 目錄 | `knowledge-base/agent_cli_file/catalogue.md` |
| 端對端測試 skill | `.ai/skills/e2e-test/SKILL.md` |
