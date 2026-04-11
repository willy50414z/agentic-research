# Agentic Research 標準作業程序 (SOP)

本文件是 Agentic Research 系統的完整功能規格，適用對象為開發者與 QA。內容涵蓋操作流程、各節點實作細節、Prompt 與 Rule 對應、已實作現況、計畫中功能，以及驗證指令與異常排查。

> **符號說明**
> - ✅ 已實作並可運行
> - 🚧 部分實作（stub / fallback）
> - 📋 計畫中（尚未實作）

---

## 目錄

1. [前置條件](#前置條件)
2. [系統架構概覽](#系統架構概覽)
3. [看板欄位說明](#看板欄位說明)
4. [spec.md 格式規範](#specmd-格式規範)
5. [流程總覽](#流程總覽)
6. [階段詳細說明](#階段詳細說明)
   - [第一階段：專案初始化](#第一階段專案初始化-initialization)
   - [第二階段：規格審查](#第二階段規格審查-spec-review)
   - [第三階段：自動化研究循環](#第三階段自動化研究循環-verify)
   - [第四階段：最終評查與結案](#第四階段最終評查與結案-final-review)
7. [技術參考](#技術參考)
   - [Resume API 使用方式](#resume-api-使用方式)
   - [完整附件與產出檔案清單](#完整附件與產出檔案清單)
   - [下一階段：Freqtrade 回測整合](#下一階段freqtrade-回測整合)
   - [DB 記錄驗證指令](#db-記錄驗證指令)
   - [異常排查](#異常排查)

---

## 前置條件

在開始任何研究流程前，先確認以下環境項目均已就緒。

### 服務健康確認

```bash
docker compose -f deploy/docker-compose.local.yml ps
```

確認以下所有服務狀態為 `running` 或 `healthy`：

| 服務名稱                 | Port |
|--------------------------|------|
| `agentic-postgres`       | 5432 |
| `agentic-framework-api`  | 7001 |
| `agentic-planka`         | 7002 |
| `agentic-minio`          | 9000 |
| `agentic-mlflow`         | 5000 |

### API 健康確認

```bash
curl -s http://localhost:7001/health
# 期望輸出：{"status":"ok"}

curl -s http://localhost:7001/health/llm
# 期望輸出：{"ok": true, "results": {...}}
```

若 `/health/llm` 回傳 `ok: false`，確認 `.env` 中的 LLM 認證設定（`LLM_CHAIN`、`ANTHROPIC_API_KEY` 等）是否正確。

### Planka Webhook 確認

登入 Planka Admin（`http://localhost:7002` → 右上角 Admin Area → Webhooks），確認存在以下 Webhook：

| 欄位   | 期望值                                             |
|--------|----------------------------------------------------|
| URL    | `http://agentic-framework-api:8000/planka-webhook` |
| Events | `cardUpdate`                                       |
| Status | Enabled                                            |

若 Webhook 不存在或未啟用，系統不會收到卡片移動事件，整個流程將無法自動觸發。

### 必要環境變數（`.env`）

| 變數                | 說明                                                          |
|---------------------|---------------------------------------------------------------|
| `LLM_CHAIN`         | 逗號分隔的 LLM provider 清單（如 `claude,gemini`），至少需要兩個 |
| `ANTHROPIC_API_KEY` | Claude 認證金鑰                                               |
| `DATABASE_URL`      | PostgreSQL 連線字串                                           |
| `MINIO_*`           | MinIO 連線設定（用於附件存儲）                                |
| `PLANKA_*`          | Planka 連線設定                                               |
| `ARTIFACTS_DIR`     | 研究產出目錄（預設 `./artifacts`）                            |

---

## 系統架構概覽

### 核心設計原則

**File-based 輸出協定（✅ 已實作）**

所有 LLM agent 節點採用「寫檔案 → framework 讀檔案」模式，不依賴 stdout 解析：
- LLM 收到 `{OUTPUT_DIR}` 參數，將結果寫入具名檔案（如 `analyze_result.txt`）
- Framework 讀取具名檔案取得結構化結果
- 優點：可重現、可 debug、不受 LLM 回應格式漂移影響

**內容注入（Content Injection）（✅ 已實作）**

規則與標準文件的內容直接注入 Prompt 變數（而非傳路徑讓 LLM 讀取）：
- `{CONSTRAINTS}` → `.ai/rules/spec-review-agent-constraints.md` 的全文
- `{RULES}` → `.ai/rules/spec-review.md` 的全文
- `{SAMPLE_SPEC}` → `framework/prompts/spec_review/sample_spec.md` 的全文
- `{RULES_PATH}` → `.ai/rules/backtest-required-metrics.md`（analyze 節點例外，以路徑傳入讓 LLM 用工具讀取）

**雙 LLM 協作（✅ 已實作）**

Spec Review 固定使用 `LLM_CHAIN` 中的兩個不同 provider，避免單一 LLM 自我確認偏誤：
- `participants[0]`（llm-1）：Author 角色，負責初稿或 Refine
- `participants[-1]`（llm-2）：Synthesizer 角色，負責最終定稿

**LangGraph StateGraph（✅ 已實作）**

研究循環以 LangGraph 實作，支援持久化 checkpoint（PostgreSQL）、中斷恢復（interrupt）。

---

## 看板欄位說明

系統在 Planka 建立以下 6 個固定欄位，卡片依流程自動或手動在欄位間移動：

| 欄位                    | 說明                                                                  |
|-------------------------|-----------------------------------------------------------------------|
| **Planning**            | 使用者準備研究需求的起點。新卡片在此建立，`spec.md` 在此上傳。      |
| **Spec Pending Review** | 使用者將卡片移入此欄以觸發 Spec Review Agent 自動審查。              |
| **Verify**              | Spec 審查通過後，系統將卡片移入此欄，自動啟動研究循環。              |
| **Review**              | 需要人工介入時（HITL 計畫審核、達到 max_loops）系統將卡片移至此欄。  |
| **Done**                | 研究通過或使用者決定結案後，系統將卡片移至此欄。                     |
| **Failed**              | 發生未預期的例外錯誤時，系統將卡片移至此欄並附上錯誤訊息。           |

---

## spec.md 格式規範

`spec.md` 是啟動研究的唯一輸入文件，系統以檔名 `spec.md`（大小寫須完全一致）識別。

### 量化交易策略必填欄位

| 欄位         | spec.md 對應區塊            | 說明                                                    |
|--------------|-----------------------------|---------------------------------------------------------|
| 市場論點     | `## Hypothesis`             | 策略假設與邏輯                                          |
| 交易標的     | `## Universe`               | Instruments、Exchange、Timeframe                        |
| 資料規格     | `## Data`                   | Source、train_timerange、val_timerange、清理規則         |
| 進場訊號     | `## Signals > Entry`        | 精確條件：指標數值、交叉、閾值                          |
| 出場訊號     | `## Signals > Exit`         | 精確條件 + 停損設定                                     |
| 倉位 Sizing  | `## Position Sizing`        | 風險規則、最大倉位                                      |
| 執行假設     | `## Execution`              | 訂單類型、滑價、手續費、延遲、再平衡頻率                |
| 績效門檻     | `## Performance Thresholds` | min win_rate、max drawdown、min alpha_ratio、profit_factor（IS/OOS） |
| Plugin       | `## Settings > Plugin`      | 固定填入 `quant_alpha`                                  |

> **欄位缺漏處理：** Spec Review Agent 依據領域最佳實務推斷預設值，並在 `## 假設說明` 中條列理由。只有「無法合理推斷的關鍵商業決策」（如實盤交易所）才列為待釐清問題並阻擋流程前進。

### 最低測試設定建議

| 項目     | 建議值                          |
|----------|---------------------------------|
| 測試標的 | `BTCUSDT`（期貨，perpetual）    |
| 測試工具 | Freqtrade                       |
| 測試時段 | `20210101`～`20260101`          |
| 資料來源 | Binance                         |

---

## 流程總覽

研究生命週期分為四個主要階段：

| 階段 | 輸入 | 動作摘要 | 輸出 | 卡片移動 |
|------|------|---------|------|----------|
| **1. 專案初始化** | `spec.md`、卡片標題 | 建立 Planka 卡片、寫入 `thread_id`、上傳 `spec.md` | Planka 卡片（含附件） | 手動停在 **Planning** |
| **2. 規格審查** | `spec.md` 附件與留言串 | 雙 LLM 審查完整性、一致性與可執行性；問題貼回卡片 | `reviewed_spec_final.md` 或「Spec 審查問題」留言 | PASS → **Verify**；FAIL → **Planning** |
| **3. 研究循環** | 通過審查的 spec | LLM 生成 Freqtrade 策略 → 回測（IS+OOS）→ 分析 → 修正，迭代循環 | `vN_researchsummary_YYYYMMDDHHMM.md`、DB 指標 | 循環中留在 **Verify**；HITL 或達 max_loops → **Review** |
| **4. 最終評查** | 循環報告與留言 | 使用者決定 terminate、continue 或 replan | 跨循環總結報告（`v1_vN_researchsummary_*.md`） | PASS/TERMINATE → **Done**；例外 → **Failed** |

---

## 階段詳細說明

### 第一階段：專案初始化 (Initialization)

**目標：** 以結構化格式定義研究範圍與假說，並在 Planka 建立對應卡片作為流程錨點。

#### 操作步驟

1. 在 Planka 的 **Planning** 欄點擊 **+ Add card**，輸入卡片標題。
   - 建議格式：`<策略名稱>-<版本>`，例如 `btc-momentum-pullback-v1`
   - 標題會被系統轉換為 `thread_id`（全小寫、符號換連字號）
2. 點開卡片，在 **Description** 欄位手動填入：
   ```
   thread_id: btc-momentum-pullback-v1
   ```
   > 若系統 Webhook 已正確設定，第一次卡片事件時系統也會自動寫入；手動填入可確保後續流程不中斷。
3. 在卡片 **Attachments** 區塊點擊 **Add attachment**，上傳 `spec.md`。
   - 檔名必須為 `spec.md`（大小寫須一致）
   - 若附件名稱錯誤，系統在 Spec Review 階段會貼出錯誤留言並將卡片移回 **Planning**

#### 檢查點

- [ ] **Planning** 欄有卡片，Description 含 `thread_id: <名稱>`
- [ ] Attachments 區顯示 `spec.md`，大小 > 0 bytes
- [ ] `spec.md` 包含測試標的、測試工具、測試時段等基本欄位

---

### 第二階段：規格審查 (Spec Review)

**目標：** 確保研究計畫邏輯嚴密、欄位完整且具備技術可執行性。

#### 觸發方式

將卡片從 **Planning** 拖曳到 **Spec Pending Review**。

> 若前一輪有待補充事項，先在卡片留言區回覆問題答案，再重新移入 **Spec Pending Review** 觸發重審。

#### LLM 分工（✅ 已實作）

Spec Review 固定由 `LLM_CHAIN` 的**兩個 provider** 協作，固定 2 輪，缺一不可：

| LLM | 角色 | 使用 Prompt | 注入內容 |
|-----|------|------------|---------|
| `participants[0]`（llm-1） | Author / Refine | `spec_agent_initial.txt` 或 `spec_agent_refine.txt` | `{CONSTRAINTS}`、`{RULES}`、`{SAMPLE_SPEC}`、`{SPEC}`、`{OUTPUT_DIR}` |
| `participants[-1]`（llm-2） | Synthesizer | `spec_agent_synthesize.txt` | `{CONSTRAINTS}`、`{RULES}`、`{SPEC}`（current）、`{OUTPUT_DIR}` |

> `LLM_CHAIN=claude,gemini` → claude 擔任 Author，gemini 擔任 Synthesizer。

#### Prompt 注入說明

| 變數 | 來源 | 說明 |
|------|------|------|
| `{CONSTRAINTS}` | `.ai/rules/spec-review-agent-constraints.md` | 行為約束全文（禁止掃描目錄、禁止問問題等） |
| `{RULES}` | `.ai/rules/spec-review.md` | 量化交易策略審查規則全文 |
| `{SAMPLE_SPEC}` | `framework/prompts/spec_review/sample_spec.md` | 完整 BTC/USDT RSI 策略範例（僅 initial prompt 使用） |
| `{SPEC}` | Planka 附件下載的 `spec.md` 全文 | initial/refine 用原始 spec；synthesize 用 `current_spec_for_review.md`（上一輪產出） |
| `{COMMENT_HISTORY}` | Planka 留言串（僅 refine 使用） | 篩選後的最後一次「Spec 審查問題」留言 + 使用者後續回覆（`_format_qa_history` 邏輯） |
| `{OUTPUT_DIR}` | 本輪工作目錄 | LLM 寫出檔案的目標路徑 |

#### Refine 路徑的 Spec 來源

- **initial 輪**：讀取原始 `spec.md`
- **refine 輪**：優先讀取上一輪審查最終稿 `reviewed_spec_final.md`（含 Synthesizer 改寫與問題列表）；若不存在則 fallback 讀 `reviewed_spec_initial.md`（首輪 Author 初稿）；兩者皆不存在時系統中止本輪審查（`status=abort`），貼出錯誤留言至卡片並將卡片移回 **Planning**
- **synthesize 輪**：讀取本輪 llm-1 的最新輸出（透過 graph state `current_spec_md` 傳入）

#### 留言歷史篩選（_format_qa_history）

Refine 路徑只傳入**最後一次審查問答交換**，避免累積過長的歷史干擾：
1. 掃描留言串，找到最後一條包含 `**Spec 審查問題**` 的留言
2. 取該留言及其後所有留言（使用者回覆）
3. 若找不到審查問題留言，回傳 `(no spec review questions found)`

#### LLM 輸出協定

所有輪次寫入同一個 `{OUTPUT_DIR}`（= `Path(spec_path).parent`），設計上不會衝突：

| 輪次 | 必寫規格檔 | 必寫狀態檔（二擇一） |
|------|-----------|---------------------|
| initial（Round 0） | `reviewed_spec_initial.md` | `status_pass.txt` **或** `status_need_update.txt` |
| refine（Round 0） | `reviewed_spec_final.md` | `status_pass.txt` **或** `status_need_update.txt` |
| synthesize（Round 1） | `reviewed_spec_final.md` | `status_pass.txt` **或** `status_need_update.txt` |

- `status_pass.txt`：規格完整，無待釐清問題（空檔或寫 `PASS`）
- `status_need_update.txt`：有待釐清問題，每行一個問題

**為何不衝突：**
- `status_pass.txt` / `status_need_update.txt`：framework 在**每輪開始前自動刪除**這兩個檔案（`unlink(missing_ok=True)`），確保讀到的狀態一定屬於當輪
- `reviewed_spec_initial.md`：只有 initial 輪寫入，其餘輪不觸碰
- `reviewed_spec_final.md`：refine 和 synthesize 都寫入，但 synthesize 透過 **graph state**（`current_spec_md`）讀取上一輪內容，不直接讀 `reviewed_spec_final.md`；synthesize 的最終覆蓋即為預期行為

#### 系統行為

1. Webhook 收到 `cardUpdate` 事件，確認卡片移入 **Spec Pending Review**。
2. 系統下載 `spec.md` 附件；偵測卡片是否有「Spec 審查問題」留言且後面有使用者回覆。
3. 依偵測結果選擇審查路徑（見下方情況 A / B / C）。
4. Round 0（llm-1）：補全欄位、填入推斷值，寫出 `reviewed_spec_initial.md`。
5. Round 1（llm-2）：以 Round 0 輸出為基礎，定稿並寫出 `reviewed_spec_final.md`，決定 PASS 或 NEED_UPDATE。
6. 審查結果上傳至卡片 Attachments，並依結果移動卡片。

#### 情況 A：審查通過（PASS）

- 卡片自動移至 **Verify**
- 附件新增 `reviewed_spec_initial.md`（Round 0）、`reviewed_spec_final.md`（Round 1）
- `reviewed_spec_final.md` 內容包含：
  - `## 對需求的理解`
  - `## 假設說明`（各推斷值與理由）
  - 量化交易策略必填欄位（含具體數值）
  - **無** `## 待釐清問題` 章節
- PASS 時系統**不會**貼留言（正常行為）

#### 情況 B：審查需補件（FAIL / NEED_UPDATE）

- 卡片移回 **Planning**
- 留言區出現 `**Spec 審查問題**` 留言，每行一個問題（來自 `status_need_update.txt`）
- 附件仍有 `reviewed_spec_final.md`，最後包含 `## 待釐清問題` 章節
- **補件後重審流程：**
  1. 在卡片留言區回覆問題答案
  2. 若需修改 spec 內容，先刪除舊 `spec.md` 附件，再上傳更新版
  3. 將卡片拖曳回 **Spec Pending Review**，觸發重審（走情況 C 路徑）

#### 情況 C：補件後重審（Refine 路徑）

當系統偵測到「卡片有 Spec 審查問題留言，且留言後方有使用者回覆」時，自動切換為 Refine 路徑：

| 輪次 | LLM | 使用 Prompt | 動作 |
|------|-----|------------|------|
| Round 0 | llm-1 | `spec_agent_refine.txt` | 優先讀取 `reviewed_spec_final.md`（上一輪審查最終稿，含 Synthesizer 改寫）+ 留言問答，整合使用者回答，若已足夠則產出新版 `reviewed_spec_final.md`（PASS）；仍有疑問則列剩餘問題（NEED_UPDATE）。若 `reviewed_spec_final.md` 不存在，fallback 讀 `reviewed_spec_initial.md` |
| Round 1 | llm-2 | `spec_agent_synthesize.txt` | 複查並確認最終版本 |

> **與首次審查的差異：** Refine 路徑的 llm-1 不重新審查整份 spec，聚焦在「使用者回答是否解決先前問題」，以及剩餘未回答的問題是否可推斷。

#### 監看 Engine Log

```bash
docker logs -f agentic-framework-api 2>&1 | grep -E "spec-review|card|project"
```

確認看到：
```
[spec-review] START  card='btc-momentum-pullback-v1' project_id='btc-momentum-pullback-v1'
[spec-review] spec.md saved to /tmp/...
```

等待時間約 30 秒～3 分鐘（依 LLM 回應速度）。

#### 檢查點

- [ ] Engine log 出現 `[spec-review] START`
- [ ] PASS：卡片在 **Verify**，Attachments 有 `reviewed_spec_final.md`，無待釐清問題
- [ ] FAIL：卡片回 **Planning**，留言有 `Spec 審查問題`，`reviewed_spec_final.md` 含 `## 待釐清問題`

---

### 第三階段：自動化研究循環 (Verify)

**目標：** 透過「計畫 → 回測 → 分析」迭代循環，依據 spec 生成並優化 Freqtrade 策略。

#### 節點與 Prompt 對應

卡片進入 **Verify** 後，系統自動啟動 `quant_alpha` plugin 的研究 Graph：

| 節點 | 狀態 | 執行者 | 使用 Prompt | 輸出檔案 |
|------|------|--------|------------|---------|
| **plan** | ✅ | LLM（Claude） | `quant_alpha/plan.txt` | `artifacts/strategies/<StrategyName>.py`、`artifacts/plan_output.json` |
| **implement** | 🚧 | Python（`backtest.py`） | — | `artifacts/loop_N_train.json` |
| **test** | 🚧 | Python（`backtest.py`） | — | graph state `test_metrics` |
| **analyze** | ✅ | LLM（Claude） | `quant_alpha/analyze.txt` | `artifacts/analyze_result.txt` |
| **revise**（FAIL 時） | ✅ | LLM（Claude） | `quant_alpha/revise.txt` | `artifacts/revise_result.txt`、`artifacts/revised_params.json` |
| **summarize**（PASS 時） | ✅ | LLM（Claude） | `quant_alpha/summarize.txt` | `artifacts/loop_summary.md` → 上傳 Planka |
| **terminate_summarize**（TERMINATE 時） | ✅ | LLM（Claude） | `quant_alpha/terminate_summary.txt` | `artifacts/termination_report.md` → 上傳 Planka |
| **final_summary**（max_loops 後） | 📋 | LLM（Claude） | `quant_alpha/final_summary.txt` | `v1_vN_researchsummary_*.md` → 上傳 Planka |

使用 Rule：analyze 節點額外讀取 `.ai/rules/backtest-required-metrics.md`（透過 `{RULES_PATH}` 傳入路徑，LLM 用工具讀取）。

#### Prompt 注入說明（quant_alpha）

| Prompt | 注入變數 |
|--------|---------|
| `plan.txt` | `{SPEC}`（`reviewed_spec_final.md` 全文）、`{loop_index}`、`{last_decision}`、`{STRATEGY_DIR}`、`{OUTPUT_DIR}` |
| `analyze.txt` | `{RULES_PATH}`、`{strategy_name}`、`{params}`、`{loop_index}`、`{win_rate}`、`{alpha_ratio}`、`{max_drawdown}`、`{profit_factor}`、`{n_trades}`、`{target_win_rate}`、`{target_profit_factor}`、`{OUTPUT_DIR}` |
| `revise.txt` | `{params}`（plan_output.json 全文）、`{reason}`、`{attempt_count}`、`{OUTPUT_DIR}` |
| `summarize.txt` | `{project_id}`、`{goal}`、`{loop_index}`、`{strategy_name}`、`{params}`、`{win_rate}`、`{alpha_ratio}`、`{max_drawdown}`、`{profit_factor}`、`{n_trades}`、`{total_return}`、`{OUTPUT_DIR}` |
| `terminate_summary.txt` | `{project_id}`、`{goal}`、`{strategy_name}`、`{terminate_reason}`、`{attempt_count}`、`{attempts_table}`、`{target_win_rate}`、`{OUTPUT_DIR}` |

#### 系統行為詳細說明

卡片進入 **Verify** 後，依序執行：

**步驟 1 — 計畫（plan）✅**

LLM 讀取 `reviewed_spec_final.md` 全文（注入為 `{SPEC}`），嚴格依照規格撰寫完整的 Freqtrade 策略 Python 類別（繼承 `IStrategy`），包含：
- `populate_indicators()`：所有技術指標計算
- `populate_entry_trend()`：進場條件（來自 spec 的精確閾值）
- `populate_exit_trend()`：出場條件（來自 spec 的精確閾值）
- `timeframe`、`stoploss`、`minimal_roi`、可優化參數（`IntParameter`、`DecimalParameter`）

策略類別寫入：`{STRATEGY_DIR}/{strategy_name}.py`（例：`artifacts/strategies/BtcRsiMomentum.py`）
元資料寫入：`{OUTPUT_DIR}/plan_output.json`，格式：

```json
{
  "strategy_name": "BtcRsiMomentum",
  "strategy_file": "artifacts/strategies/BtcRsiMomentum.py",
  "timeframe": "1h",
  "stoploss": -0.05,
  "parameters": {"rsi_period": 14, "rsi_buy": 35.0, "rsi_sell": 70.0},
  "_reason": "首輪依 spec 實作：RSI 14 期，買入 < 35，賣出 > 70"
}
```

**步驟 2 — 人機協作暫停（HITL）🚧**

> **目前狀態：** `needs_human_approval` 硬編碼為 `False`，HITL 暫停已停用。Freqtrade 整合後恢復啟用，讓使用者在看到策略程式碼後確認是否繼續。

計畫中行為：
1. 卡片移至 **Review**，貼出計畫審核留言
2. 使用者審核策略程式碼後，將卡片拖回 **Verify** 或呼叫 Resume API（`action: approve`）

**步驟 3 — 實作 IS 回測（implement）🚧**

> **目前為 stub 實作：** `backtest.py` 以固定種子的隨機數模擬回測指標（`win_rate`、`profit_factor`、`alpha_ratio`、`max_drawdown`、`n_trades`、`total_return`），種子由 `n_bars + strategy_name + params` 決定，確保相同輸入產生相同輸出。

計畫中（Freqtrade 整合後）：
- 呼叫 `freqtrade_backtest_executor.py`，以 Freqtrade CLI 執行 IS 回測
- IS 期間對應 spec 的 `train_timerange`（例：`20210101-20240101`）
- Freqtrade CLI 產出 `.zip` 壓縮檔至 `user_data/backtest_results/`
- 呼叫 `analyze_backtest_result.py` 解析 `.zip`，提取 IS 指標：`winrate`、`profit_factor`、`max_drawdown_account`、`sharpe`、`sortino`、`calmar`、`profit_total_pct`
- 結果存為 `artifacts/loop_N_train.json`

**步驟 4 — 測試 OOS 回測（test）🚧**

> **目前為 stub 實作：** 同 implement，以稍微不同的 `n_bars`（`300 + attempt * 50`）調用 `backtest.py`，模擬 OOS 指標。

計畫中（Freqtrade 整合後）：
- 對應 spec 的 `val_timerange`（例：`20240101-20260101`）
- 提取 OOS 指標（與 IS 相同欄位集合）
- analyze 節點將同時收到 IS + OOS 兩組指標進行評估

**步驟 5 — 分析（analyze）✅**

LLM 讀取 `.ai/rules/backtest-required-metrics.md`（用工具讀取，不注入）取得評估門檻，對照回測指標決定：

通過條件（全部達到才算 PASS）：
| 指標 | 門檻 | 來源 |
|------|------|------|
| `win_rate` | ≥ spec 中的 `target_win_rate`（預設 0.55） | spec `## Performance Thresholds` |
| `alpha_ratio` | ≥ 1.0 | 固定門檻 |
| `max_drawdown` | ≤ 0.20 | 固定門檻 |
| `profit_factor` | ≥ spec 中的 `target_profit_factor`（預設 1.2） | spec `## Performance Thresholds` |

LLM 寫出 `artifacts/analyze_result.txt`：
```
PASS
win_rate=0.58 ≥ 0.55, alpha_ratio=1.12 ≥ 1.0, drawdown=0.15 ≤ 0.20, profit_factor=1.35 ≥ 1.2
```
第 1 行：`PASS`、`FAIL` 或 `TERMINATE`；第 2 行：一句話說明哪些指標通過或未通過。

**TERMINATE 條件：** 策略結構性失敗（如 n_trades=0），或多次嘗試後仍重複相同失敗模式。

每輪 loop 分析完成後分支：
- **PASS** → summarize 節點
- **FAIL** → revise 節點 → 下一次 implement → test → analyze
- **TERMINATE** → terminate_summarize 節點

**步驟 6a — 修正（revise，FAIL 時）✅**

LLM 收到失敗原因與當前 `plan_output.json` 參數，調整指標參數後寫出：
- `artifacts/revise_result.txt`（第 1 行：`REVISED` 或 `TERMINATE`；第 2 行：調整說明）
- `artifacts/revised_params.json`（與 `plan_output.json` 格式相同的調整後參數）

調整範圍：`parameters` 中的指標閾值/週期、`stoploss`、`minimal_roi`（不改變策略類型）。

若 LLM 不可用，rule-based fallback：每次將 `stoploss` 縮小 0.01（最多至 -0.02）。

最多嘗試 3 次（`attempt >= 3` 時強制 TERMINATE）。

**步驟 6b — 摘要（summarize，PASS 時）✅**

LLM 撰寫本輪 Markdown 研究報告，寫出 `artifacts/loop_summary.md`，第一行格式：
```
_摘要：<一句話>_
```
系統讀取 `_摘要：` 行作為 Planka 卡片留言摘要，並上傳完整報告為 `vN_researchsummary_YYYYMMDDHHMM.md`。

**步驟 6c — 終止報告（terminate_summarize，TERMINATE 時）✅**

LLM 撰寫終止報告，寫出 `artifacts/termination_report.md`，上傳至卡片。

#### HITL 計畫審核（計畫中，目前停用）

> **目前 `needs_human_approval` 固定為 `False`**，以下為 Freqtrade 整合後的預期行為：

每個 loop 的 `plan` 節點完成後，系統暫停：
1. 卡片自動移至 **Review** 欄
2. 卡片貼出留言，含生成的策略程式碼路徑與審核說明
3. 使用者確認後，將卡片從 **Review** 拖回 **Verify** 或呼叫 Resume API

#### 循環結果留言格式

每次 `analyze` 節點完成，系統在卡片貼出本輪摘要：

```
Loop 1 完成 — FAIL
  win_rate: 24.1% (目標 ≥ 45%) ❌
  profit_factor: 0.70 (目標 ≥ 1.2) ❌
  alpha_ratio: 0.85 (目標 ≥ 1.0) ❌
  max_drawdown: 42.6% (目標 ≤ 20%) ❌
  → 下一輪改善：優化出場結構，收緊停損
```

或：

```
Loop 2 完成 — PASS
  win_rate: 47.5% ✅
  profit_factor: 1.25 ✅
  alpha_ratio: 1.12 ✅
  max_drawdown: 12.3% ✅
```

#### 附件產生規則

| 條件 | 上傳至 Planka 的附件 |
|------|---------------------|
| 每個 PASS loop | `vN_researchsummary_YYYYMMDDHHMM.md`（來自 `loop_summary.md`） |
| TERMINATE | `termination_report.md` |
| 達到 max_loops | `v1_vN_researchsummary_*.md`（來自 `final_summary.txt`，📋 計畫中） |
| FAIL loop | 不產生 summary 附件 |

#### 檢查點

- [ ] Engine log 出現研究 Graph 啟動訊息
- [ ] `artifacts/strategies/` 目錄出現 `<StrategyName>.py` 策略檔
- [ ] `artifacts/plan_output.json` 存在且格式正確
- [ ] `artifacts/analyze_result.txt` 第 1 行為 `PASS`、`FAIL` 或 `TERMINATE`
- [ ] 每個 PASS loop 後，Planka Attachments 新增 `vN_researchsummary_*.md`
- [ ] loop 指標記錄於 DB `loop_metrics` 表（透過 MLflow）

---

### 第四階段：最終評查與結案 (Final Review)

**目標：** 使用者檢視循環報告，決定繼續迭代或結案。

#### 觸發時機

達到 `max_loops` 上限後，系統：
1. 自動產出跨循環對比報告（📋 `final_summary.txt`，計畫中）並上傳至卡片
2. 將卡片移至 **Review** 並貼出總結留言

#### 操作步驟

1. 開啟 **Review** 欄的卡片
2. 閱讀關鍵附件：
   - `v1_vN_researchsummary_YYYYMMDDHHMM.md`（跨循環績效對比）
   - `reviewed_spec_final.md`（最後通過審查的研究規格）
3. 決策（在 Planka 手動拖曳卡片）：
   - **繼續迭代** → 拖曳卡片到 **Spec Pending Review**（觸發 Phase 2 重審，再進入新一輪研究循環）
   - **結案** → 拖曳卡片到 **Done**

#### 各結案路徑

- **繼續 → Spec Pending Review → Verify：** spec 再次經過審查（可選擇更新 spec.md）
- **結案 → Done：** 研究正式結束
- **例外錯誤 → Failed：** 系統自動將卡片移至 **Failed**，留言含錯誤摘要

#### 檢查點

- [ ] 卡片在 **Review**，有跨循環對比報告
- [ ] MLflow 實驗中包含所有循環的執行紀錄
- [ ] 繼續：卡片移至 **Spec Pending Review**；結案：卡片移至 **Done**

---

## 技術參考

### Resume API 使用方式

> **適用範圍：** Resume API 用於 HITL 計畫審核（approve）。Phase 4 的結案與繼續迭代請在 Planka 手動拖曳卡片操作。
>
> **目前狀態（🚧）：** HITL 暫停已停用（`needs_human_approval=False`），Resume API 保留供 Freqtrade 整合後使用。

**Endpoint：** `POST http://localhost:7001/resume`

```bash
curl -s -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "btc-momentum-pullback-v1",
    "decision": {"action": "approve"}
  }' | python -m json.tool
```

**期望回應：** `{"status": "resumed"}`

| action    | 說明                                              |
|-----------|---------------------------------------------------|
| `approve` | HITL 計畫審核通過（等同手動拖曳卡片回 **Verify**） |

---

### 完整附件與產出檔案清單

#### 上傳至 Planka 的附件

| 附件名稱 | 來源（Prompt） | 產生條件 |
|----------|--------------|---------|
| `spec.md` | 使用者初始上傳 | 必定存在 |
| `reviewed_spec_initial.md` | `spec_agent_initial.txt`（llm-1，Round 0） | 必定存在 |
| `reviewed_spec_final.md` | `spec_agent_synthesize.txt`（llm-2，Round 1） | 必定存在 |
| `llm_response_initial.txt` | Round 0 LLM 原始回應 | 視設定 |
| `llm_response_synthesize.txt` | Round 1 LLM 原始回應 | 視設定 |
| `llm_response_refine.txt` | Refine 路徑 LLM 原始回應 | 補件重審時 |
| `vN_researchsummary_YYYYMMDDHHMM.md` | `quant_alpha/summarize.txt` | 每個 PASS loop |
| `termination_report.md` | `quant_alpha/terminate_summary.txt` | TERMINATE 時 |
| `v1_vN_researchsummary_YYYYMMDDHHMM.md` | `quant_alpha/final_summary.txt` | 結案時（📋 計畫中） |

#### 內部工作目錄（`artifacts/`）— 不上傳 Planka

| 檔案 | 來源 | 說明 |
|------|------|------|
| `strategies/<StrategyName>.py` | `plan.txt`（LLM） | Freqtrade IStrategy 策略類別 |
| `plan_output.json` | `plan.txt`（LLM） | 策略元資料（名稱、參數、stoploss） |
| `loop_N_train.json` | `implement`（Python） | IS 回測結果 |
| `analyze_result.txt` | `analyze.txt`（LLM） | 第 1 行：PASS/FAIL/TERMINATE；第 2 行：原因 |
| `revise_result.txt` | `revise.txt`（LLM） | 第 1 行：REVISED/TERMINATE；第 2 行：調整說明 |
| `revised_params.json` | `revise.txt`（LLM） | 調整後的策略元資料（與 `plan_output.json` 同格式） |
| `loop_summary.md` | `summarize.txt`（LLM） | 本輪 Markdown 研究報告（上傳後覆蓋） |
| `termination_report.md` | `terminate_summary.txt`（LLM） | 終止報告 |

#### Spec Review 流控檔案 — 不上傳 Planka

| 檔案 | 說明 |
|------|------|
| `status_pass.txt` | 規格完整，無待釐清問題 |
| `status_need_update.txt` | 有待釐清問題，每行一個 |
| `current_spec_for_review.md` | synthesize 輪使用的暫存 spec（llm-1 輸出的當前版本） |

---

### 下一階段：Freqtrade 回測整合

> **狀態：📋 計畫中**

本節記錄 implement/test 節點的 Freqtrade 整合設計，供下一階段開發參考。

#### 整合架構

```
plan.txt（LLM）
  └→ artifacts/strategies/<StrategyName>.py
        │
        ├─ implement 節點（IS 回測）
        │    └→ freqtrade_backtest_executor.py
        │         --strategy <StrategyName>
        │         --timerange <train_timerange>
        │         └→ user_data/backtest_results/*.zip
        │              └→ analyze_backtest_result.py → IS 指標
        │
        └─ test 節點（OOS 回測）
             └→ freqtrade_backtest_executor.py
                  --strategy <StrategyName>
                  --timerange <val_timerange>
                  └→ user_data/backtest_results/*.zip
                       └→ analyze_backtest_result.py → OOS 指標
```

#### 關鍵模組

| 模組 | 位置 | 說明 |
|------|------|------|
| `freqtrade_backtest_executor.py` | `E:\code\binance\...\freqtrade\` | 呼叫 Freqtrade CLI（`freqtrade backtesting`），產出 `.zip` |
| `cross_test_runner.py` | 同上 | IS/OOS 分割執行器；以 `train_timerange` + `val_timerange` 分別執行 |
| `analyze_backtest_result.py` | 同上 | 解析 Freqtrade `.zip`，提取結構化指標 |

#### Freqtrade 回測指標（整合後可用）

| 指標 | 對應欄位 | 說明 |
|------|---------|------|
| `winrate` | `win_rate` | 勝率 |
| `profit_factor` | `profit_factor` | 總獲利 / 總虧損 |
| `max_drawdown_account` | `max_drawdown` | 最大帳戶回撤 |
| `profit_total_pct` | `total_return` | 總報酬率（%） |
| `sharpe` | — | Sharpe Ratio（整合後新增評估） |
| `sortino` | — | Sortino Ratio（整合後新增評估） |
| `calmar` | — | Calmar Ratio（整合後新增評估） |
| `trade_count` | `n_trades` | 交易次數 |

#### IS/OOS 評估升級（整合後）

analyze 節點收到 IS + OOS 雙組指標後：
- 同時評估 IS 與 OOS 的 `win_rate`、`profit_factor`、`max_drawdown`
- OOS 門檻可設定為 IS 門檻的 80%（保守驗證）
- 防止過度擬合：IS PASS 但 OOS 大幅落差 → FAIL

#### 整合所需程式碼變更

| 檔案 | 變更說明 |
|------|---------|
| `projects/quant_alpha/plugin.py` | `implement_node` / `test_node` 改為呼叫 Freqtrade CLI |
| `projects/quant_alpha/backtest.py` | 以 `freqtrade_backtest_executor.py` + `analyze_backtest_result.py` 取代 stub |
| `framework/prompts/quant_alpha/analyze.txt` | 新增 IS/OOS 雙組指標變數（`{is_win_rate}`、`{oos_win_rate}` 等） |
| `projects/quant_alpha/plugin.py`（analyze_node） | 傳入 IS/OOS 分組指標至 analyze prompt |

---

### DB 記錄驗證指令

#### 查詢 loop_metrics（循環指標）

```bash
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "SELECT loop_index, result, reason FROM loop_metrics \
      WHERE project_id = 'btc-momentum-pullback-v1' \
      ORDER BY loop_index;"
```

**期望：** 每個 loop 都有一筆記錄，`loop_index` 不重複，`result` 為 `PASS` 或 `FAIL`。

#### 查詢 projects 記錄

```bash
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "SELECT id, name, plugin_name FROM projects \
      WHERE id = 'btc-momentum-pullback-v1';"
```

**期望：** 有一筆記錄，`plugin_name = 'quant_alpha'`。

#### 查詢 checkpoints（LangGraph 狀態）

```bash
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "SELECT thread_id, checkpoint_id, created_at FROM checkpoints \
      WHERE thread_id = 'btc-momentum-pullback-v1' \
      ORDER BY created_at DESC LIMIT 5;"
```

#### 清除測試資料

```bash
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "DELETE FROM loop_metrics   WHERE project_id = 'btc-momentum-pullback-v1';
      DELETE FROM projects        WHERE id         = 'btc-momentum-pullback-v1';
      DELETE FROM checkpoints     WHERE thread_id  = 'btc-momentum-pullback-v1';"
```

完成後，在 Planka 手動刪除對應卡片（卡片 → 右上角 ⋯ → Delete card）。

---

### 異常排查

#### 卡片停在 Spec Pending Review 超過 5 分鐘

```bash
docker logs --tail=100 agentic-framework-api | grep -E "spec-review|ERROR|WARNING"
```

| 症狀 | 原因 | 解法 |
|------|------|------|
| log 無 `[spec-review] START` | Webhook 未觸發 | 確認 Planka Admin → Webhooks 設定正確（URL、Events、Status） |
| log 有 `no spec.md attachment` | 附件名稱錯誤 | 刪除舊附件，重新上傳正確命名的 `spec.md` |
| log 有 `LLM error` | LLM 認證失敗 | 執行 `docker exec -it agentic-framework-api claude auth login` 或更新 `.env` |
| 超過 5 分鐘後卡片自動回 Planning | 排程器清除 stale review flag | 重新拖曳卡片到 **Spec Pending Review** |

#### 附件未出現

```bash
docker logs agentic-framework-api | grep -E "Uploaded work_dir|Failed to upload"
```

- 若看到 `Failed to upload`：確認 Planka token 有效且未過期。
- 若看到 `Uploaded work_dir`：附件已上傳，重新整理 Planka 頁面。

#### 卡片在 Verify 無動靜

```bash
docker logs agentic-framework-api | grep "moved to list.*Verify"
```

- 無 log：Planka Webhook 未設定或 `cardUpdate` event 未啟用。
- 有 log 但顯示 `thread_id not found`：卡片 Description 缺少 `thread_id:` 行，手動補上後再移動一次卡片。

#### analyze_result.txt 未產生（LLM 寫檔失敗）

```bash
ls artifacts/analyze_result.txt
```

若不存在：
- 確認 Claude CLI 已登入（`claude auth status`）
- 確認 `ARTIFACTS_DIR` 目錄存在且可寫入
- Rule-based fallback 會自動接管，log 中看到 `rule-based fallback`

#### 查看完整 Engine Log

```bash
# 即時追蹤
docker logs -f agentic-framework-api

# 過濾關鍵事件
docker logs agentic-framework-api 2>&1 | grep -E "spec-review|QuantAlpha|HITL|ERROR|stale"

# 排程器清除 stale review 紀錄
docker logs agentic-framework-api | grep "stale review_in_progress"
```
