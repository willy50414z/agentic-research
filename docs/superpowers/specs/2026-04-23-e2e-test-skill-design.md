# E2E Test Skill 設計文件

**日期**：2026-04-23  
**狀態**：待實作  
**放置路徑**：`.ai/skills/e2e-test/`

---

## 對需求的理解

此 skill 用於對 agentic-research 框架進行端對端整合測試。它自動建立 Planka 卡片、上傳測試策略文件、驅動完整 pipeline 從 Spec Pending Review 跑到 Done/Failed/Review，並在每個里程碑執行嚴格的斷言，將全部過程記錄在一份 progress.md 中供使用者 review。

---

## 目標

- 一個 prompt（`/e2e-test`）即可觸發完整測試
- 測試結束後有一份完整的 progress.md，每個 checklist item 有 ✅/❌ 與實際擷取值
- 支援 `BACKTEST_MODE=mock`（預設，快速流程驗證）與 `BACKTEST_MODE=real`（真實 Freqtrade 回測）
- 統計資料直接從 artifact JSON 檔案擷取，不只依賴 Planka comment

---

## 檔案結構

```
.ai/skills/e2e-test/
├── SKILL.md
└── scripts/
    ├── poll_until.py        # adaptive polling + log 擷取，輸出 JSON
    └── extract_metrics.py   # 讀 artifact JSON，輸出統計摘要 JSON
```

**測試執行產出物（每次執行建立獨立目錄）：**

```
.ai/e2e-test-runs/
└── YYYYMMDD-HHMMSS/
    ├── progress.md           # 主測試紀錄文件
    └── logs/
        ├── spec-review.log   # spec review 階段 server log
        └── research.log      # research graph 階段 server log
```

---

## 環境需求

從 `.env` 讀取以下變數（skill 啟動時驗證）：

| 變數 | 必填 | 說明 |
|------|------|------|
| `PLANKA_API_URL` | ✅ | Planka API 位址，例如 `http://localhost:7002` |
| `PLANKA_TOKEN` | ✅ | Planka Bearer token |
| `PLANKA_BOARD_ID` | ✅ | 目標看板 ID |
| `DATABASE_URL` | ✅ | PostgreSQL 連線字串 |
| `BACKTEST_MODE` | ✅ | `mock` 或 `real`（預設 `mock`） |
| `LOG_SOURCE` | 選填 | `file:/path/to/server.log` 或 `docker:<container_name>`；未設定則跳過 log 擷取 |

---

## 六個執行階段

### Phase 1 — 前置確認

**目標**：確認所有依賴服務正常，API server 已啟動。

**步驟：**
1. 讀取 `.env` 確認必填環境變數存在
2. 執行 `docker compose ps`，確認 `postgres`、`planka`、`minio` 均為 healthy
3. 呼叫 `GET http://localhost:8002/health`：
   - 若 200 → API 已啟動
   - 若連線拒絕 → 執行 `python main.py > .ai/e2e-test-runs/<run_id>/server.log 2>&1 &`，等待最多 30 秒直到 `/health` 回 200
4. 呼叫 `GET http://localhost:8002/health/llm`，記錄 LLM provider 狀態

**任一失敗 → 記錄原因、abort 測試、progress.md 標記 Phase 1 FAIL。**

---

### Phase 2 — Setup（建立測試卡片）

**目標**：在 Planka 建立測試卡片，上傳策略文件，觸發 webhook。

**步驟：**
1. 產生 `thread_id`：格式 `e2e-test-HHMMSS`
2. `POST /api/cards` 在 Planning column 建立卡片：
   - `name`：`[E2E Test] Turtle Trading HHMMSS`
   - `description`：`thread_id: e2e-test-HHMMSS`
3. 記錄回傳的 `card_id`
4. 讀取 `tests/README.md`，以 `spec.md` 為檔名上傳為卡片附件（`POST /api/cards/{card_id}/attachments`）
5. 確認附件上傳回傳 200/201
6. 呼叫 `PATCH /api/cards/{card_id}` 將卡片移至 **Spec Pending Review** column（觸發 webhook）

**斷言：**
- 卡片 `card_id` 非空
- 附件上傳 HTTP status 為 200 或 201
- PATCH 移動 HTTP status 為 200

---

### Phase 3 — Spec Review 監測

**目標**：等待 spec_review_graph 完成，擷取 log。

**執行 `poll_until.py`：**

```bash
python .ai/skills/e2e-test/scripts/poll_until.py \
  --card-id <card_id> \
  --target-columns "Verify,Planning" \
  --timeout 900 \
  --interval-early 30 \
  --interval-late 60 \
  --early-window 300 \
  --log-source "${LOG_SOURCE}" \
  --log-grep "SPEC.REVIEW\|spec_review\|spec-review" \
  --log-output .ai/e2e-test-runs/<run_id>/logs/spec-review.log
```

**輸出 JSON：**
```json
{
  "status": "reached",
  "column": "Verify",
  "elapsed_seconds": 312,
  "log_lines": 45
}
```

**timeout 行為**：15 分鐘到期後若卡片未移動，記錄 TIMEOUT 並繼續執行斷言（標記為 ❌）。

---

### Phase 4 — Spec Review 斷言

**目標**：確認 spec review 結果符合預期。

| # | 項目 | 通過條件 | 失敗行為 |
|---|------|---------|---------|
| 4-1 | 卡片在 Verify column | `column == "Verify"` | ❌ 記錄實際 column，後續 phase 繼續（但標記整體 FAIL）|
| 4-2 | Planka comment 含 PASS 標記 | 任一 comment 含 `[SPEC-REVIEW] PASS` | ❌ 列出實際 comment 清單 |
| 4-3 | Comment 含 plugin 資訊 | 含 `plugin: quant_alpha` | ❌ |
| 4-4 | 附件含初稿 | `reviewed_spec_initial.md` 存在 | ❌ |
| 4-5 | 附件含終稿 | `reviewed_spec_final.md` 存在 | ❌ |
| 4-6 | Log 含節點進入記錄 | log 含 `[NODE ENTER] SPEC_REVIEW_INIT` | ⚠️ 警告（LOG_SOURCE 未設定時跳過）|

**Progress.md 記錄範例：**
```markdown
- ✅ 4-1 卡片在 Verify column
- ✅ 4-2 Planka comment 含 [SPEC-REVIEW] PASS — plugin=quant_alpha, hypothesis=海龜交易
- ✅ 4-3 Comment 含 plugin: quant_alpha
- ✅ 4-4 附件含 reviewed_spec_initial.md — 大小：2.8 KB
- ✅ 4-5 附件含 reviewed_spec_final.md — 大小：3.2 KB
- ✅ 4-6 Log 含 [NODE ENTER] SPEC_REVIEW_INIT — 共擷取 38 行
```

---

### Phase 5 — Research Graph 監測

**目標**：等待 research graph 執行至終止狀態（Done/Failed/Review），擷取 log。

**Adaptive polling 策略：**
- 前 5 分鐘：每 30 秒 poll 一次
- 5 分鐘後：每 2 分鐘 poll 一次
- 總 timeout：30 分鐘

```bash
python .ai/skills/e2e-test/scripts/poll_until.py \
  --card-id <card_id> \
  --target-columns "Done,Failed,Review" \
  --timeout 1800 \
  --interval-early 30 \
  --interval-late 120 \
  --early-window 300 \
  --log-source "${LOG_SOURCE}" \
  --log-grep "NODE ENTER\|NODE EXIT\|ROUTE\|QuantAlpha" \
  --log-output .ai/e2e-test-runs/<run_id>/logs/research.log
```

---

### Phase 6 — Research 斷言 + 最終報告

**目標**：確認研究結果符合預期，擷取 artifact 統計資料。

#### 6-A Planka 狀態斷言

| # | 項目 | 通過條件 |
|---|------|---------|
| 6-1 | 最終 column 在預期範圍 | `column in ("Done", "Failed", "Review")` |
| 6-2 | Loop metrics comment 存在 | 任一 comment 含 `last_result=` |
| 6-3 | Research summary 附件存在 | 附件名稱符合 `v*_researchsummary_*.md` |
| 6-4 | Log 含完整節點執行記錄 | log 含 `[NODE ENTER] PLAN`、`IMPLEMENT`、`TEST`、`ANALYZE` |

#### 6-B Artifact JSON 統計擷取（透過 `extract_metrics.py`）

**Mock 模式（`BACKTEST_MODE=mock`）：**
```bash
python .ai/skills/e2e-test/scripts/extract_metrics.py \
  --mode mock \
  --artifacts-dir ./artifacts \
  --output .ai/e2e-test-runs/<run_id>/metrics_summary.json
```

期望讀取：`artifacts/loop_0_train.json`

驗證欄位存在：`win_rate`、`profit_factor`、`max_drawdown`、`n_trades`、`total_return`

**Real 模式（`BACKTEST_MODE=real`）：**
```bash
python .ai/skills/e2e-test/scripts/extract_metrics.py \
  --mode real \
  --artifacts-dir ./artifacts \
  --output .ai/e2e-test-runs/<run_id>/metrics_summary.json
```

期望讀取：`artifacts/.llm_io/*/loop_0_is.json` + `loop_0_oos.json`

驗證欄位存在：IS 和 OOS 各有 `win_rate`、`profit_factor`、`max_drawdown`、`n_trades`

額外 sanity check（非 pass 標準，僅記錄）：
- `OOS profit_factor >= IS profit_factor * 0.6`（過擬合警告）
- `OOS win_rate >= IS win_rate * 0.6`（過擬合警告）

**`metrics_summary.json` 格式：**
```json
{
  "mode": "mock",
  "loops_found": 1,
  "loop_0": {
    "win_rate": 0.6234,
    "profit_factor": 1.45,
    "max_drawdown": 0.12,
    "n_trades": 47,
    "total_return": 0.21
  }
}
```

---

## Progress.md 完整結構

```markdown
# E2E Test Run — YYYY-MM-DD HH:MM:SS

## 環境
- run_id: YYYYMMDD-HHMMSS
- thread_id: e2e-test-HHMMSS
- card_id: <planka_card_id>
- BACKTEST_MODE: mock | real
- LOG_SOURCE: docker:agentic-framework-api | file:... | (未設定)
- API: http://localhost:8002
- Planka: http://localhost:7002

## Phase 1 — 前置確認
- ✅/❌ docker compose: postgres healthy
- ✅/❌ docker compose: planka healthy
- ✅/❌ docker compose: minio healthy
- ✅/❌ API /health 回傳 200
- ✅/❌ API /health/llm — providers: [claude, gemini]

## Phase 2 — Setup
- ✅/❌ 卡片建立成功 — card_id: xxx
- ✅/❌ spec.md 上傳成功 — 大小: N KB
- ✅/❌ 卡片移至 Spec Pending Review

## Phase 3 — Spec Review 監測
- 等待時間: N 秒
- 最終 column: Verify | Planning | TIMEOUT
- 擷取 log 行數: N 行

## Phase 4 — Spec Review 斷言
- ✅/❌ 4-1 卡片在 Verify column
- ✅/❌ 4-2 [SPEC-REVIEW] PASS comment 存在 — 內容摘要
- ✅/❌ 4-3 plugin: quant_alpha
- ✅/❌ 4-4 附件 reviewed_spec_initial.md
- ✅/❌ 4-5 附件 reviewed_spec_final.md
- ✅/⚠️ 4-6 Log 節點記錄

## Phase 5 — Research 監測
- 等待時間: N 秒
- 最終 column: Done | Failed | Review | TIMEOUT

## Phase 6 — Research 斷言
- ✅/❌ 6-1 最終 column 在預期範圍 — 實際: Done
- ✅/❌ 6-2 loop metrics comment 存在
- ✅/❌ 6-3 researchsummary 附件存在 — 檔名: v1_researchsummary_202604231503.md
- ✅/⚠️ 6-4 Log 節點執行記錄

## Artifact 統計（extract_metrics.py 輸出）
| 指標 | IS | OOS |
|------|-----|-----|
| win_rate | 0.62 | 0.58 |
| profit_factor | 1.89 | 1.34 |
| max_drawdown | 0.11 | 0.14 |
| n_trades | 52 | 23 |
| ⚠️ 過擬合警告 | OOS pf = IS * 0.71 ≥ 0.6 ✅ |

## 擷取的 Log 片段
### Spec Review
```
（[SPEC-REVIEW] 相關行，最多 50 行）
```
### Research Graph
```
（[NODE ENTER/EXIT]、[ROUTE]、[QuantAlpha] 相關行，最多 100 行）
```

## 最終結果
**整體判定**：PASS / FAIL  
**通過項目**：N / M  
**失敗項目清單**：
- 4-2: [SPEC-REVIEW] PASS comment 未找到
**耗時**：Spec Review 4m32s / Research 12m18s / 總計 17m22s  
**備註**：
```

---

## `poll_until.py` 介面規格

**輸入參數：**
```
--card-id         Planka 卡片 ID
--target-columns  逗號分隔的目標 column 名稱（任一達到即停止）
--timeout         總等待秒數（預設 900）
--interval-early  前段 polling 間隔秒數（預設 30）
--interval-late   後段 polling 間隔秒數（預設 120）
--early-window    前段結束時間點秒數（預設 300，即前 5 分鐘）
--log-source      LOG_SOURCE 字串（未設定時跳過 log 擷取）
--log-grep        grep pattern（Python re 格式）
--log-output      log 輸出路徑
```

**輸出 JSON（stdout）：**
```json
{
  "status": "reached | timeout",
  "column": "目前 column 名稱",
  "elapsed_seconds": 312,
  "log_lines": 45,
  "error": null
}
```

**從 Planka API 取得 column：** `GET /api/cards/{card_id}` → `item.listId`，再對照 `GET /api/boards/{board_id}` 的 `included.lists`。

---

## `extract_metrics.py` 介面規格

**輸入參數：**
```
--mode            mock | real
--artifacts-dir   artifacts 目錄路徑（預設 ./artifacts）
--output          輸出 JSON 路徑
```

**Mock 模式讀取路徑：**
- `{artifacts_dir}/loop_*_train.json`（glob，取最新）

**Real 模式讀取路徑：**
- `{artifacts_dir}/.llm_io/*/loop_*_is.json`（glob，取最新）
- `{artifacts_dir}/.llm_io/*/loop_*_oos.json`（glob，取最新）

**輸出 JSON：** 參見 Phase 6-B 格式。

---

## 觸發 Prompt

```
/e2e-test
```

SKILL.md 中記錄的觸發說明：
> 執行 agentic-research 框架端對端整合測試。從建立 Planka 卡片開始，驅動完整 pipeline（Spec Review → Research Graph）至第一輪結束，嚴格驗證每個里程碑，並將全部記錄寫入 progress.md。

---

## 不在此 skill 範圍內

- ~~多輪研究迴圈測試（第一輪結束即完成）~~ — revise v2 pipeline 上線後須擴展為至少 `max_loops=2`，見下方驗證點
- LLM 輸出品質評估（只驗證流程正確性與結構）
- Planka board 初始化（前置條件：board 已由 `/init-planka-board` 建立）
- 效能基準測試

---

## Revise v2 Pipeline 驗證點（max_loops=2 案例）

當 `REVISE_PIPELINE_VERSION=v2` 啟用後，e2e-test 須將 `max_loops` 設為 2 並擴增以下斷言。這些驗證點是 `revise-pipeline-checklist-audit` change 的核心驗收條件之一。

### V2-1 每輪策略 `.py` 為 byte-different

跑完 `max_loops=2` 後，確認 `artifacts/strategies/v0/` 與 `artifacts/strategies/v1/` 各存在恰好一份 `.py` 檔，且兩份檔案 byte-different（用 `hashlib.sha256` 或 `cmp` 比對）。

- 通過條件：`sha256(v0/{Strategy}.py) != sha256(v1/{Strategy}.py)`
- 失敗常見根因：revise 階段沒真的重寫 `.py`、`shutil.copy2` 退回舊版本（即原始 bug）

### V2-2 Backtest metrics 不同

`v0` 與 `v1` 兩輪的 IS/OOS metrics（win_rate、profit_factor、max_drawdown 至少一項）數值不同。

- 通過條件：`metrics(v0) != metrics(v1)`，比對 `loop_0` 與 `loop_1` 對應 JSON
- 失敗常見根因：策略 `.py` 雖被重寫但執行參數未變更（檢查是否 deterministic check 漏抓）

### V2-3 Planka audit 附件存在

每輪 revise 完成後須有對應 audit markdown 上傳至 Planka：

- `v0_audit.md`（baseline 不適用，跳過）
- `v1_audit.md` 必存在
- 若有 `v2`：`v2_audit.md` 必存在

通過條件：每個有觸發 revise 的 iteration N，Planka 附件含 `v{N}_audit.md`。

### V2-4 Staging 目錄保留

確認 `artifacts/.staging/v{N}/` 在 TERMINATE 時保留（非 promote 時不被清理）。

- 若任一輪 revise TERMINATE：對應 `artifacts/.staging/v{N}/` 仍存在 `candidate.py` 與 `audit_report_attempt_*.yaml`
- 若全 revise APPROVED：staging 仍保留；正式路徑 `artifacts/strategies/v{N}/` 同時存在

### V2-5 Per-iteration spec 與 direction 上傳

每輪 revise 完成後，Planka 附件須含：

- `v{N}_strategy_spec.md`（從通過 audit 的 `.py` 結構性萃取產出）
- `v{N}_revised_direction.md`（Stage A intent APPROVED 後的最終文案）

baseline 僅須 `v0_strategy_spec.md`（無 direction，因無 revise）。

### V2-6 SKILL.md 與 design 同步

`.ai/skills/e2e-test/SKILL.md` 若已建立，其 checklist 須涵蓋上述 V2-1 到 V2-5；若尚未建立，本設計文件作為驗證點的 canonical 來源，未來 SKILL.md 落地時須同步引入。

### Progress.md 對應條目

```markdown
## Phase 6 — Revise v2 驗證（僅 max_loops>=2 啟用）
- ✅/❌ V2-1 v0/v1 .py byte-different — sha256(v0)=..., sha256(v1)=...
- ✅/❌ V2-2 v0/v1 backtest metrics 不同 — pf=A vs B, wr=C vs D
- ✅/❌ V2-3 Planka 含 v1_audit.md（與 v2_audit.md if any）
- ✅/❌ V2-4 staging 目錄保留 — 路徑: artifacts/.staging/v1/...
- ✅/❌ V2-5 v0_strategy_spec.md / v1_strategy_spec.md / v1_revised_direction.md 上傳成功
```

---

## 假設說明

- **thread_id 格式**：`e2e-test-HHMMSS`，足夠唯一不需 UUID（一天內不會重複）
- **Planning column 存在**：board 已正確初始化，六個 column 均存在
- **Freqtrade CLI 已安裝**：`BACKTEST_MODE=real` 時 skill 不檢查安裝，由 backtest 執行失敗時的錯誤 comment 反映
- **artifacts 目錄路徑**：使用 `ARTIFACTS_DIR` env var（預設 `./artifacts`）
- **API server port**：8002（與 `main.py` 一致）
- **Log 行數上限**：spec-review.log 保留最多 200 行，research.log 最多 500 行（避免 progress.md 過大）
