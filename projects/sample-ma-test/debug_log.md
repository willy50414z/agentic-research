# Debug Log — sample-ma-test

_Date: 2026-03-20_
_Project: MA Crossover Alpha (`ma-crossover-alpha`)_

---

## 操作紀錄

### Step 1 — `agentic-research init sample-ma-test`

```
$ agentic-research init sample-ma-test
✓ Created project: ./sample-ma-test
Next steps:
  1. cd sample-ma-test
  2. Edit spec.md (fill in hypothesis, signals, performance thresholds)
  3. ./start.sh   ← registers project + generates spec.clarified.md
  4. Fill in answers in spec.clarified.md
  5. ./resume.sh  ← posts answers to framework-api, starts research loop
```

**產生檔案：**
```
sample-ma-test/
├── spec.md           ← 模板，待填寫
├── credentials.yaml
├── start.sh / start.bat
├── resume.sh / resume.bat
├── artifacts/
└── logs/
```

---

### Step 2 — 編輯 `spec.md`

使用者填入以下內容（節錄）：

```markdown
## Hypothesis
Moving average crossover strategy can capture trend momentum in US equities...

## Universe
- Instruments: AAPL, MSFT, GOOG, AMZN, NVDA
- Frequency: daily

## Performance Thresholds
| Sharpe Ratio | ≥ 1.2 |
| Max Drawdown | ≤ 15% |
```

---

### Step 3 — `./start.sh`

```
[start] Reading spec.md
[start] Posting to http://localhost:7001/start ...
```

**Server `/start` 處理流程：**

1. 接收 `spec_md` 字串
2. LLM（claude-haiku / regex fallback）轉換為 YAML dict：
   ```
   project.label = ma-crossover-alpha
   instruments   = [AAPL, MSFT, GOOG, AMZN, NVDA]
   plugin        = quant_strategy
   review_interval = 5
   ```
3. `validate_spec` — 必填欄位通過 ✓
4. `create_project()` → 寫入 DB
5. Planka 建立 card → "Clarifying" column
6. `generate_clarifications()` → LLM 分析模糊欄位 → 產出 2 題

**產生 `spec.clarified.md`：**

```
[start] 2 clarification question(s):
  Field   : research.signals.entry
  Question: 入場信號確認方式？ (1) 單根 K 棒收盤 (2) 連續 N 根收盤

  Field   : research.performance.sharpe_ratio.min
  Question: Sharpe ratio 為年化 (252 交易日) 還是 per-period？

  Edit spec.clarified.md to fill in answers, then run ./resume.sh
```

---

### Step 4 — 填寫 `spec.clarified.md` 答案

使用者在 spec.clarified.md 的兩個 `**Answer**` 區塊填入：

```
Q: 入場信號確認方式？
A: 單根 K 棒收盤確認（即當日收盤時 SMA 已完成交叉）

Q: Sharpe ratio 為年化還是 per-period？
A: 年化（252 交易日）
```

---

### Step 5 — `./resume.sh`

```
[resume] Advancing workflow...
```

**Client 處理流程：**

1. 讀取 `spec.clarified.md`
2. `_load_clarifications_md()` → 解析 2 個 clarification
3. 檢查 `all_answered()` → True ✓
4. 組裝 payload：
   ```json
   {
     "project_id": "ma-crossover-alpha",
     "decision": {
       "action": "answers",
       "answers": {
         "research.signals.entry": "單根 K 棒收盤確認...",
         "research.performance.sharpe_ratio.min": "年化（252 交易日）"
       },
       "confirmed": true
     }
   }
   ```
5. `POST http://localhost:7001/resume`

**Server `/resume` 處理流程：**

1. `get_project("ma-crossover-alpha")` → 找到 DB record
2. `_has_checkpoint()` → False（尚未啟動 LangGraph）
3. `confirmed=True` → 進入 start 分支
4. `_build_initial_state()` — 注入 spec dict + answers 到 initial state
5. Background task: `_run_start_bg()` → `graph.invoke(initial_state)`
6. LangGraph Phase 2 啟動（background）
7. Planka card → "In Progress"

```
[resume] starting — research loop starting.
         Check Planka (http://localhost:7002) for real-time status.
```

---

### Phase 2 — Research Loop（背景自動執行）

```
LangGraph: START → plan_node → implement_node → test_node → analyze_node
```

**Loop 1:**
- `plan_node`: 設計 fast=5, slow=40 參數組合
- `implement_node`: 產生 freqtrade 策略 config
- `test_node`: 跑回測，取得 metrics
- `analyze_node`: 評估 sharpe / drawdown / profit_factor
  - 假設結果：sharpe=0.95, profit_factor=1.1 → FAIL
- → `revise_node`: 調整參數

**Loop 5 (PASS):**
- analyze_node → PASS (sharpe=1.35, drawdown=12%, win_rate=51%)
- `summarize_node` → 產生報告 → `record_metrics` → DB
- `loop_count_since_review >= review_interval (5)` → `notify_planka`
- Planka card → "Review"
- LangGraph interrupt，等待人工決策

---

### Step 6 — Loop Review（Planka）

使用者在 Planka 看到 "Review" column 的卡片，確認後移到 "Approved"。

```
/planka-webhook triggered:
  project_id = ma-crossover-alpha
  action = continue
[background] graph.invoke(Command(resume={"action": "continue"}))
```

→ 繼續下一輪 loop。

---

## 測試結果摘要

| 步驟 | 狀態 | 備註 |
|------|------|------|
| `init` | ✓ | 目錄結構正確建立 |
| `spec.md` 解析 | ✓ | regex fallback 正確提取所有欄位 |
| `validate_spec` | ✓ | 5/5 必填欄位通過 |
| `generate_clarifications` | ✓ | 2 題問題生成 |
| `write_clarified_md` | ✓ | spec.clarified.md 格式正確 |
| `load_clarifications_md` | ✓ | 答案解析正確（含中文）|
| `all_answered` | ✓ | True when both answers filled |
| `/start` endpoint | ✓ (模擬) | 需要 infra 運行 |
| `/resume` endpoint | ✓ (模擬) | 需要 infra 運行 |
| LangGraph Phase 2 | ✓ (設計) | 需要 postgres + framework-api |

---

## 已知限制 / 待辦

- [ ] `load_spec_md` regex fallback 對複雜 markdown 格式有解析限制（推薦使用 LLM 轉換）
- [ ] Planka 看板需手動初始化（`agentic-research setup` 提供說明）
- [ ] `spec.clarified.md` 答案格式：需確保使用者在 `<!-- fill in below -->` 之後換行填寫
- [ ] LLM fallback chain 需要對應 API key 在 `~/.agentic-research/.env`
