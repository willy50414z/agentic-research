# Agentic Research 開發驗證 Checklist

> **用途**：逐階段驗證系統功能完整性，供 agent 依序執行「驗證 → 修正 → 重測」。
>
> **符號說明**
> - ✅ 已驗證通過
> - ❌ 驗證失敗（需修正）
> - ⬜ 尚未執行
> - 🚧 已知 stub，待整合
> - 📋 尚未實作，需新增

---

## 目錄

1. [Phase 0 — 環境健康](#phase-0--環境健康)
2. [Phase 1 — 專案初始化](#phase-1--專案初始化)
3. [Phase 2 — Spec Review](#phase-2--spec-review)
4. [Phase 3 — Research Loop](#phase-3--research-loop)
5. [Phase 4 — Final Review 與結案](#phase-4--final-review-與結案)
6. [TODO A — Freqtrade 真實回測整合](#todo-a--freqtrade-真實回測整合)
7. [清理測試資料](#清理測試資料)

---

## Phase 0 — 環境健康

> 每次開始新一輪驗證前先跑此區。所有項目 PASS 才繼續。

### P0-01 Docker 服務健康

```bash
docker compose -f deploy/docker-compose.local.yml ps
```

預期所有服務狀態為 `running` 或 `healthy`：

| 服務 | Port |
|------|------|
| `agentic-postgres` | 5432 |
| `agentic-framework-api` | 7001 |
| `agentic-planka` | 7002 |
| `agentic-minio` | 9000 |
| `agentic-mlflow` | 5000 |

- [ ] ⬜ 所有服務 healthy

### P0-02 API 健康確認

```bash
curl -s http://localhost:7001/health
# 期望：{"status":"ok"}

curl -s http://localhost:7001/health/llm
# 期望：{"ok": true, "results": {...}}
```

- [ ] ⬜ `/health` 回傳 `status: ok`
- [ ] ⬜ `/health/llm` 回傳 `ok: true`（確認 LLM 認證有效）

**失敗排查**：
- `ok: false` → 確認 `.env` 中 `LLM_CHAIN`、`ANTHROPIC_API_KEY` 設定正確
- docker logs 看詳細錯誤：`docker logs agentic-framework-api --tail 50`

### P0-03 Planka Webhook 設定

登入 `http://localhost:7002` → Admin Area → Webhooks，確認：

| 欄位 | 期望值 |
|------|--------|
| URL | `http://agentic-framework-api:8000/planka-webhook` |
| Events | `cardUpdate` |
| Status | Enabled |

- [ ] ⬜ Webhook 存在且已啟用

### P0-04 環境變數完整性

```bash
docker exec agentic-framework-api env | grep -E "LLM_CHAIN|ANTHROPIC|DATABASE_URL|MINIO|PLANKA|ARTIFACTS_DIR"
```

- [ ] ⬜ `LLM_CHAIN` 含至少兩個 provider（如 `claude,gemini`）
- [ ] ⬜ `ANTHROPIC_API_KEY` 有值
- [ ] ⬜ `DATABASE_URL` 有值
- [ ] ⬜ `MINIO_*` 系列有值
- [ ] ⬜ `PLANKA_API_URL`、`PLANKA_TOKEN`、`PLANKA_BOARD_ID` 有值

### P0-05 Unit Test 基線確認

```bash
cd E:/code/agentic-research
pytest tests/test_positive_flows.py -v -m "not integration" 2>&1 | tail -20
```

- [ ] ⬜ 所有 unit test PASSED（無 DB 需求的部分）

```bash
pytest tests/test_positive_flows.py -v -m integration 2>&1 | tail -20
```

- [ ] ⬜ 所有 integration test PASSED（需要 DATABASE_URL）

---

## Phase 1 — 專案初始化

> 驗證卡片建立流程與 spec.md 上傳機制。

### P1-01 Code Check：Planka 欄位建立

**檔案**：`framework/api/server.py`

確認 `_ensure_planka_columns()` 在啟動時建立以下六欄（順序固定）：
`Planning` → `Spec Pending Review` → `Verify` → `Review` → `Done` → `Failed`

```bash
# 確認 Planka board 有這六欄（需登入 Planka UI 人工確認）
# 或查 server.py 的 _COL_* 常數
grep "_COL_" framework/api/server.py
```

- [ ] ⬜ 六個欄位常數定義齊全，與 SOP 一致

### P1-02 Code Check：thread_id 格式化

**檔案**：`framework/api/server.py`，搜尋 `thread_id` 轉換邏輯。

確認卡片標題轉為 `thread_id` 的規則：全小寫、符號換連字號（`-`）。

```bash
grep -n "thread_id" framework/api/server.py | head -20
```

- [ ] ⬜ 格式化邏輯存在且符合規格

### P1-03 E2E：卡片建立與 spec.md 上傳

**前置**：準備一份有效的 `spec.md`（參考 `framework/prompts/spec_review/sample_spec.md`）

**步驟**：
1. 在 Planka **Planning** 欄建立測試卡片，標題：`dev-checklist-test-v1`
2. 在 Description 填入：`thread_id: dev-checklist-test-v1`
3. 上傳 `spec.md` 附件（檔名必須完全一致）

**檢查**：
```bash
# 確認 DB 有 project 記錄（在觸發 Spec Review 後）
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "SELECT id, name, plugin_name FROM projects WHERE id = 'dev-checklist-test-v1';"
```

- [ ] ⬜ Planning 欄有卡片
- [ ] ⬜ Description 含 `thread_id: dev-checklist-test-v1`
- [ ] ⬜ Attachments 顯示 `spec.md`，大小 > 0 bytes

---

## Phase 2 — Spec Review

> 驗證雙 LLM 審查流程的正確性。分三個子情況：PASS、NEED_UPDATE、Refine（補件重審）。

### P2-01 Code Check：status 檔案 cleanup

**檔案**：`framework/spec_review_graph.py`

確認每輪審查前，`status_pass.txt` / `status_need_update.txt` 兩個檔案都會被刪除（`unlink(missing_ok=True)`），避免讀到舊輪次的狀態。

```bash
grep -n "unlink\|status_pass\|status_need_update" framework/spec_review_graph.py
```

- [ ] ⬜ 兩個 status 檔案在每輪前都有 unlink 呼叫

### P2-02 Code Check：`_format_qa_history` 篩選邏輯

**檔案**：`framework/spec_review_graph.py:142`

確認邏輯：
1. 找到最後一條含 `**Spec 審查問題**` 的留言
2. 取該留言及其後所有留言
3. 若找不到，回傳固定字串 `(no spec review questions found)`

```bash
grep -n "_format_qa_history\|QUESTION_MARKER\|last_q_index" framework/spec_review_graph.py
```

- [ ] ⬜ 邏輯與 SOP 規格一致
- [ ] ⬜ 找不到問題留言時回傳正確 fallback 字串

### P2-03 Code Check：LLM 分工與 Prompt 注入

**檔案**：`framework/spec_review_graph.py`

確認：
- `participants[0]`（llm-1）= Author（initial/refine）
- `participants[-1]`（llm-2）= Synthesizer
- `{CONSTRAINTS}` 注入來自 `.ai/rules/spec-review-agent-constraints.md`
- `{RULES}` 注入來自 `.ai/rules/spec-review.md`
- `{SAMPLE_SPEC}` 只在 initial prompt 使用

```bash
grep -n "CONSTRAINTS\|SAMPLE_SPEC\|participants\[0\]\|participants\[-1\]" framework/spec_review_graph.py | head -20
```

- [ ] ⬜ participant index 正確（0 = Author，-1 = Synthesizer）
- [ ] ⬜ Prompt 注入變數與 SOP 表格一致
- [ ] ⬜ SAMPLE_SPEC 只注入 initial，不注入 refine/synthesize

### P2-04 Code Check：Refine 路徑的 spec 來源

**檔案**：`framework/spec_review_graph.py`

確認 refine 輪優先讀取 `reviewed_spec_final.md`（上一輪審查最終稿，含 Synthesizer 改寫與問題列表），不重讀原始 `spec.md`。`reviewed_spec_final.md` 不存在時 fallback 讀 `reviewed_spec_initial.md`（首輪 Author 初稿）。兩者皆不存在時系統 abort 並貼錯誤留言。

此設計確保多輪 refine 時，每輪都從前一輪已整合的最終稿繼續推進，不會遺失已解答的問題。

```bash
grep -n "reviewed_spec_final\|reviewed_spec_initial\|abort\|refine" framework/spec_review_graph.py | head -25
```

- [ ] ⬜ refine 路徑優先讀 `reviewed_spec_final.md`，不重讀原始 spec
- [ ] ⬜ `reviewed_spec_final.md` 不存在時正確 fallback 讀 `reviewed_spec_initial.md`
- [ ] ⬜ 兩個檔案都不存在時正確 abort 並貼留言

### P2-05 E2E：情況 A — 審查 PASS

**測試 spec**：準備欄位完整的 `spec.md`（BTC/USDT RSI 策略，含所有必填欄位）

**步驟**：
1. 使用 P1-03 建立的卡片（或新建），上傳完整 spec.md
2. 將卡片從 **Planning** 拖到 **Spec Pending Review**
3. 等待約 1～3 分鐘

**監看 log**：
```bash
docker logs -f agentic-framework-api 2>&1 | grep -E "spec-review|card|PASS|NEED_UPDATE"
```

**期望結果**：
- [ ] ⬜ log 出現 `[spec-review] START`
- [ ] ⬜ 卡片自動移至 **Verify**
- [ ] ⬜ Attachments 新增 `reviewed_spec_initial.md`
- [ ] ⬜ Attachments 新增 `reviewed_spec_final.md`
- [ ] ⬜ `reviewed_spec_final.md` 無 `## 待釐清問題` 章節
- [ ] ⬜ `reviewed_spec_final.md` 含 `## 假設說明` 章節
- [ ] ⬜ 卡片無新增留言（PASS 時系統不貼留言）

### P2-06 E2E：情況 B — 審查 NEED_UPDATE

**測試 spec**：準備欄位不完整的 `spec.md`（例如缺少 `## Execution` 或含模糊描述）

**步驟**：
1. 新建卡片，上傳不完整 spec.md
2. 拖到 **Spec Pending Review**

**期望結果**：
- [ ] ⬜ 卡片移回 **Planning**
- [ ] ⬜ 留言區出現 `**Spec 審查問題**`，每行一個問題
- [ ] ⬜ `reviewed_spec_final.md` 含 `## 待釐清問題` 章節
- [ ] ⬜ `status_need_update.txt` 問題與留言逐字一致

### P2-07 E2E：情況 C — 補件重審（Refine 路徑）

**前置**：完成 P2-06（卡片在 Planning，有 Spec 審查問題留言）

**步驟**：
1. 在卡片留言區回覆所有問題答案
2. 將卡片再次拖回 **Spec Pending Review**

**監看 log**：
```bash
docker logs -f agentic-framework-api 2>&1 | grep -E "refine|has_pending_qa|synthesize"
```

**期望結果**：
- [ ] ⬜ log 出現 `has_pending_qa=True`（確認走 refine 路徑）
- [ ] ⬜ Round 0 使用 `spec_agent_refine.txt` prompt（而非 initial）
- [ ] ⬜ Round 1 使用 `spec_agent_synthesize.txt` prompt
- [ ] ⬜ PASS → 卡片移至 Verify，附件有最終 `reviewed_spec_final.md`
- [ ] ⬜ NEED_UPDATE → 卡片移回 Planning，只問剩餘未解決的問題

### P2-08 Code Check：Abort 場景

確認以下兩個 abort 情境都有正確處理（貼留言 + 移回 Planning）：

```bash
grep -n "abort\|no spec.md\|LLM_CHAIN.*empty" framework/spec_review_graph.py framework/api/server.py | head -20
```

- [ ] ⬜ 找不到 `spec.md` 附件時正確 abort
- [ ] ⬜ `LLM_CHAIN` 為空時正確 abort

---

## Phase 3 — Research Loop

> 卡片進入 Verify 後，驗證 plan → implement → test → analyze 迭代循環。
> **注意**：`implement` / `test` 目前為 stub（見 `projects/quant_alpha/backtest.py`）。

### P3-01 Code Check：Graph 結構完整性

**檔案**：`framework/graph.py`

確認 graph 邊連接正確：
```
START → plan → implement → test → analyze
analyze --(PASS)--> summarize → record_metrics → END
analyze --(FAIL)--> revise → implement
analyze --(TERMINATE)--> record_terminate_metrics → terminate_summarize → final_summary → END
```

```bash
grep -n "add_edge\|add_conditional_edges\|add_node" framework/graph.py
```

- [ ] ⬜ PASS 路徑：`analyze → summarize → record_metrics → END`
- [ ] ⬜ FAIL 路徑：`analyze → revise → implement`（loop back）
- [ ] ⬜ TERMINATE 路徑：`analyze → record_terminate_metrics → terminate_summarize → final_summary → END`

### P3-02 Code Check：analyze wrapper — max_loops 強制

**檔案**：`framework/graph.py:76`（`_make_analyze_wrapper`）

確認邏輯：
- `attempt_index >= max_loops` 且 result != PASS → override 為 `TERMINATE`，reason 以 `EXHAUSTED:` 開頭
- LLM 在 max_loops 前主動回傳 TERMINATE → override 為 `FAIL`（強制跑滿輪次）
- 從 implement/revise 傳入的 TERMINATE（pre_terminate=True）→ 不 override，直接通過

```bash
pytest tests/test_positive_flows.py::TestAnalyzeWrapper -v
```

- [ ] ⬜ TC-P09-01：FAIL 在 max_loops 前不轉 TERMINATE
- [ ] ⬜ TC-P09-02：FAIL 在第 max_loops 輪轉 EXHAUSTED TERMINATE
- [ ] ⬜ TC-P09-03：LLM 提前 TERMINATE → override 為 FAIL
- [ ] ⬜ TC-P09-04：propagated TERMINATE（plan rejection）不被 override
- [ ] ⬜ TC-P09-05：PASS 永遠通過不被修改
- [ ] ⬜ TC-P09-06：attempt_index 每輪遞增 1

### P3-03 Code Check：plan_node 輸出協定

**檔案**：`projects/quant_alpha/plugin.py:118`

確認：
- LLM 呼叫後讀取 `{OUTPUT_DIR}/plan_output.json`
- JSON 含 `strategy_name`、`strategy_file`、`timeframe`、`stoploss`、`parameters`
- LLM 不可用時有 fallback（`FallbackRsiMomentum`）

```bash
grep -n "plan_output.json\|fallback\|strategy_name" projects/quant_alpha/plugin.py | head -20
```

- [ ] ⬜ plan_output.json 路徑正確
- [ ] ⬜ fallback 路徑存在且不會 crash

### P3-04 Code Check：analyze_node 輸出協定

**檔案**：`projects/quant_alpha/plugin.py:225`

確認：
- 讀取 `{OUTPUT_DIR}/analyze_result.txt`
- 第 1 行：`PASS` / `FAIL` / `TERMINATE`（大寫）
- 第 2 行：原因說明
- LLM 不可用時走 `_rule_based_analyze()`（比對 4 個門檻）
- 無效的 result 值 fallback 為 `FAIL`

```bash
grep -n "analyze_result.txt\|_rule_based_analyze\|PASS.*FAIL.*TERMINATE" projects/quant_alpha/plugin.py | head -15
```

- [ ] ⬜ 讀檔邏輯正確
- [ ] ⬜ fallback 比對邏輯涵蓋 4 個門檻（win_rate、alpha_ratio、max_drawdown、profit_factor）
- [ ] ⬜ 無效 result 轉 FAIL

### P3-05 Code Check：revise_node 最大重試限制

**檔案**：`projects/quant_alpha/plugin.py:314`

確認：
- `attempt >= 3` → 直接回傳 TERMINATE，不呼叫 LLM
- LLM 回傳 TERMINATE → 也直接 TERMINATE（不寫 revised_params.json）
- 正常 revise：寫 `revise_result.txt` + `revised_params.json`

```bash
grep -n "attempt >= 3\|REVISED\|revised_params" projects/quant_alpha/plugin.py | head -15
```

- [ ] ⬜ attempt >= 3 時強制 TERMINATE
- [ ] ⬜ revise_result.txt 第 1 行：`REVISED` 或 `TERMINATE`

### P3-06 Code Check：summarize / terminate_summarize wrapper 上傳

**檔案**：`framework/graph.py:118`（`_make_summarize_wrapper`）
**檔案**：`framework/graph.py:164`（`_make_terminate_summarize_wrapper`）

確認：
- PASS：上傳為 `v{attempt_index}_researchsummary_{YYYYMMDDHHMM}.md`
- TERMINATE：上傳為 `v{attempt_index}_researchsummary_{YYYYMMDDHHMM}.md`
- 上傳後本地 summary 檔案刪除（`Path(path).unlink`）

```bash
pytest tests/test_positive_flows.py::TestSummarizeWrapper -v
pytest tests/test_positive_flows.py::TestTerminateSummarizeWrapper -v 2>/dev/null || echo "class not found"
```

- [ ] ⬜ TC-P05：PASS 報告上傳為正確命名格式
- [ ] ⬜ TC-P06：TERMINATE 報告也正確上傳（非 PASS 路徑）
- [ ] ⬜ 上傳後本地檔案被刪除

### P3-07 Code Check：final_summary 節點

**檔案**：`framework/graph.py:206`（`_make_final_summary_node`）
**Prompt**：`framework/prompts/quant_alpha/final_summary.txt`

確認：
- 從 DB `get_loop_metrics(project_id)` 取得所有輪次資料
- LLM 可用時用 final_summary.txt prompt 生成報告
- LLM 不可用時用 `_fallback_summary()` 生成純文字報告
- 上傳為 `v1_v{n}_researchsummary_{YYYYMMDDHHMM}.md`

```bash
grep -n "final_summary\|get_loop_metrics\|v1_v" framework/graph.py | head -20
```

- [ ] ⬜ prompt 檔案存在且格式正確（`{goal}`, `{n}`, `{rows_text}` 變數）
- [ ] ⬜ LLM fallback 邏輯存在
- [ ] ⬜ 上傳檔名格式：`v1_v{n}_researchsummary_*.md`

### P3-08 Code Check：FAIL loop metrics 記錄到 DB

**檔案**：`framework/graph.py:100`

確認：每次 analyze 結果為 FAIL 時，立即呼叫 `record_loop_metrics(result="FAIL")`（不等 TERMINATE 才記錄）。

```bash
pytest tests/test_positive_flows.py -v -k "TC_P08 or fail_loop_metrics or record" 2>/dev/null
# 或
pytest tests/test_positive_flows.py -v -m integration -k "metrics" 2>/dev/null
```

- [ ] ⬜ TC-P08：FAIL loop 寫入 DB（integration test）

### P3-09 E2E：直接 PASS（TC-P01）

```bash
pytest tests/test_positive_flows.py -v -k "P01 or direct_pass or always_pass" -m integration
```

**若無對應 E2E test，手動執行**：
1. 卡片在 Verify（P2-05 完成後）
2. 等待 loop 執行

**期望**：
- [ ] ⬜ `artifacts/strategies/<StrategyName>.py` 存在
- [ ] ⬜ `artifacts/plan_output.json` 存在且 JSON 格式正確
- [ ] ⬜ `artifacts/analyze_result.txt` 第 1 行為 `PASS` / `FAIL` / `TERMINATE`
- [ ] ⬜ PASS loop 後 Planka 附件新增 `v1_researchsummary_*.md`
- [ ] ⬜ TERMINATE 後卡片移至 **Review**
- [ ] ⬜ TERMINATE 後 Planka 附件新增 `v1_v{n}_researchsummary_*.md`（final summary）

### P3-10 E2E：Planka 循環留言格式

每次 analyze 完成後，確認卡片留言格式正確：

```
Loop 1 完成 — FAIL
  win_rate: 24.1% (目標 ≥ 45%) ❌
  profit_factor: 0.70 (目標 ≥ 1.2) ❌
  ...
```

- [ ] ⬜ 留言格式含 loop 編號、result、各指標值與目標、改善說明

### P3-11 DB 驗證：loop_metrics 記錄完整

```bash
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "SELECT loop_index, result, reason, win_rate, profit_factor FROM loop_metrics \
      WHERE project_id = 'dev-checklist-test-v1' \
      ORDER BY loop_index;"
```

- [ ] ⬜ 每個 loop 都有一筆記錄（PASS、FAIL、TERMINATE 都記錄）
- [ ] ⬜ `loop_index` 無重複，從 1 開始（1-based）
- [ ] ⬜ `result` 欄位值為 `PASS` / `FAIL` / `TERMINATE`

---

## Phase 4 — Final Review 與結案

### P4-01 E2E：max_loops 觸發移至 Review

**前置**：設定 `max_loops=1` 執行完整流程

**期望**：
- [ ] ⬜ 達到 max_loops 後，`last_reason` 以 `EXHAUSTED:` 開頭
- [ ] ⬜ 卡片自動移至 **Review**
- [ ] ⬜ 卡片有跨循環總結報告 `v1_v{n}_researchsummary_*.md`

### P4-02 E2E：Done 路徑

1. 研究循環結束，卡片在 **Review**
2. 人工拖曳卡片至 **Done**

- [ ] ⬜ 卡片移至 Done 後系統無非預期行為（Webhook 不觸發研究流程）

### P4-03 E2E：Failed 路徑

**觸發方式**：模擬例外錯誤（例如 DB 斷線或 LLM 認證失敗）

```bash
docker logs agentic-framework-api --tail 50 | grep -E "Failed|ERROR|exception"
```

- [ ] ⬜ 例外發生時卡片移至 **Failed**
- [ ] ⬜ Failed 卡片含錯誤摘要留言

### P4-04 E2E：繼續迭代路徑

1. 卡片在 **Review**
2. 人工拖曳至 **Spec Pending Review**

- [ ] ⬜ 觸發 Spec Review 重審（走 initial 或 refine 路徑）
- [ ] ⬜ 通過後進入新的研究循環（loop_index 從 0 重新開始）

### P4-05 DB 驗證：MLflow 實驗記錄

```bash
# 開啟 MLflow UI
# http://localhost:5000
# 確認實驗名稱 = project_id，各 loop 都有 run
```

- [ ] ⬜ MLflow 實驗含所有 loop 的 run
- [ ] ⬜ 各 run 有 `strategy_type`、`loop_result` params 及指標 metrics

---

## TODO A — Freqtrade 真實回測整合

> **狀態**：`projects/quant_alpha/backtest.py` 目前為 stub。
> 完成後需重新執行 Phase 3 所有驗證項目。

### T-A01 📋 implement_node 改接 Freqtrade CLI

**目標檔案**：`projects/quant_alpha/backtest.py`、`projects/quant_alpha/plugin.py`

**參考**：
- `E:/code/binance/.../freqtrade/freqtrade_backtest_executor.py`（Freqtrade CLI 呼叫）
- `E:/code/binance/.../freqtrade/cross_test_runner.py`（IS/OOS 分割執行）
- `E:/code/binance/.../freqtrade/analyze_backtest_result.py`（解析 `.zip` 提取指標）

**實作要點**：
- IS 回測：對應 spec `train_timerange`，產出 `artifacts/loop_N_train.json`
- OOS 回測：對應 spec `val_timerange`
- 解析 Freqtrade `.zip` 輸出，提取：`winrate`、`profit_factor`、`max_drawdown_account`、`profit_total_pct`、`trade_count`

**驗證**：
- [ ] ⬜ `run_backtest()` 呼叫真實 Freqtrade CLI，不依賴隨機數
- [ ] ⬜ IS `loop_N_train.json` 格式正確
- [ ] ⬜ 相同輸入（strategy + timerange）產出相同指標（可重現）

### T-A02 📋 analyze prompt 升級 IS/OOS 雙組指標

**目標**：`framework/prompts/quant_alpha/analyze.txt`、`projects/quant_alpha/plugin.py:analyze_node`

**實作要點**：
- 新增 prompt 變數：`{is_win_rate}`、`{oos_win_rate}`、`{is_profit_factor}`、`{oos_profit_factor}` 等
- OOS 門檻設為 IS 門檻的 80%（保守驗證）
- IS PASS 但 OOS 大幅落差 → FAIL（防止過度擬合）

**驗證**：
- [ ] ⬜ analyze prompt 含 IS/OOS 雙組變數
- [ ] ⬜ plugin.py analyze_node 傳入 IS/OOS 分組指標
- [ ] ⬜ OOS 落差過大時正確判定 FAIL

### T-A03 📋 Freqtrade 資料可用性確認

```bash
# 確認策略輸出目錄可被 Freqtrade CLI 找到
ls artifacts/strategies/
# 確認 Freqtrade user_data 目錄存在
ls user_data/backtest_results/ 2>/dev/null || echo "needs setup"
```

- [ ] ⬜ `artifacts/strategies/` 目錄存在
- [ ] ⬜ Freqtrade CLI 可在容器內執行
- [ ] ⬜ Binance 歷史資料已下載至 `user_data/data/binance/`

---

## 清理測試資料

每輪驗證完成後，清除測試資料以免干擾下一輪：

```bash
# 清除 DB 記錄
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "DELETE FROM loop_metrics   WHERE project_id = 'dev-checklist-test-v1';
      DELETE FROM projects        WHERE id         = 'dev-checklist-test-v1';
      DELETE FROM checkpoints     WHERE thread_id  = 'dev-checklist-test-v1';"

# 清除 artifacts
rm -rf artifacts/strategies/ artifacts/*.json artifacts/*.txt artifacts/*.md

# Planka：手動刪除測試卡片（卡片 → ⋯ → Delete card）
```

---

## 進度追蹤

| Phase | 項目數 | 通過 | 失敗 | 未執行 |
|-------|--------|------|------|--------|
| P0 環境健康 | 5 | 0 | 0 | 5 |
| P1 專案初始化 | 3 | 0 | 0 | 3 |
| P2 Spec Review | 8 | 0 | 0 | 8 |
| P3 Research Loop | 11 | 0 | 0 | 11 |
| P4 Final Review | 5 | 0 | 0 | 5 |
| TODO A Freqtrade | 3 | 0 | 0 | 3 |
| **合計** | **35** | **0** | **0** | **35** |
