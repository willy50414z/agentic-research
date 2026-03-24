# QA 手動測試指南 — Agentic Research 正向流程

> **適用對象：** 新進 QA 工程師
> **目標：** 從頭到尾完整驗證一個 research 的正向流程（PASS → Review → Done）
> **預估時間：** 30～60 分鐘（依 LLM 回應速度而定）

---

## 前置條件確認

在開始測試前，請確認以下事項均已完成（由 RD 協助設定）：

- [ ] Docker Desktop 正在執行
- [ ] `.env` 已設定完成（`PLANKA_TOKEN`、`PLANKA_BOARD_ID` 已填入）
- [ ] 所有 Docker 服務已啟動

```bash
# 在 deploy/ 目錄執行，確認所有容器狀態為 healthy / running
docker compose -f deploy/docker-compose.local.yml ps
```

**期望輸出：** 以下服務全部顯示 `running` 或 `healthy`

| 服務 | 容器名稱 | Port |
|------|----------|------|
| PostgreSQL | `agentic-postgres` | 5432 |
| LangGraph Engine | `agentic-framework-api` | 7001 |
| Planka | `agentic-planka` | 7002 |
| MinIO | `agentic-minio` | 9000/9001 |
| MLflow | `agentic-mlflow` | 5000 |

---

## 測試環境資訊

| 項目 | 值 |
|------|----|
| Planka 看板 | http://localhost:7002 |
| Planka 帳號 | `agentic@local.dev` |
| Planka 密碼 | `agentic-planka-pwd` |
| LangGraph API | http://localhost:7001 |
| MLflow UI | http://localhost:5000 |
| MinIO Console | http://localhost:9001 |

---

## 測試場景：RSI 動量策略正向流程

此測試涵蓋以下完整路徑：

```
建立專案 → Planning
 → 上傳 spec.md → Spec Pending Review
 → AI 審查通過 → Verify（研究循環自動執行）
 → 達到 max_loops → Review（等待人工決策）
 → 決策 terminate → Done
```

---

## Step 1：登入 Planka 看板

1. 開啟瀏覽器，前往 http://localhost:7002
2. 輸入帳號 `agentic@local.dev`，密碼 `agentic-planka-pwd`，點擊 **Login**
3. 首次登入會出現服務條款頁面，點擊 **Accept** 繼續
4. 確認已進入 **Research Workflow** Board，看到以下 6 個欄位：

   ```
   Planning | Spec Pending Review | Verify | Review | Done | Failed
   ```

**檢查點 1：** ✅ 成功登入，看到 6 個欄位

---

## Step 2：建立新專案（透過 API）

開啟終端機，執行以下指令建立新的研究專案卡片：

```bash
curl -s -X POST http://localhost:7001/projects \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "qa-test-rsi-001",
    "plugin": "quant_alpha",
    "max_loops": 3
  }' | python -m json.tool
```

**期望回應：**
```json
{
  "project_id": "qa-test-rsi-001",
  "status": "created",
  "card_id": "<some-id>"
}
```

> 若指令回傳 `{"detail": "project already exists"}`，請先清除舊資料（見[附錄A](#附錄a清除測試資料)）或改用不同的 `project_id`（如 `qa-test-rsi-002`）。

**檢查點 2：** ✅ API 回傳 `"status": "created"`

---

## Step 3：確認 Planka 卡片出現在 Planning 欄

回到 Planka 看板：

1. 重新整理瀏覽器（F5）
2. 確認 **Planning** 欄出現一張新卡片，標題類似 `qa-test-rsi-001`
3. 點開卡片，確認說明（Description）中有 `thread_id: qa-test-rsi-001`
4. 確認 Custom Fields 中 `max_loops` 欄位值為 **3**

**檢查點 3：** ✅ Planning 欄有新卡片，Description 含 thread_id，max_loops = 3

---

## Step 4：準備 spec.md 檔案

在本機建立一個 `spec.md` 檔案，內容如下：

```markdown
# Research Spec

## Hypothesis
使用 RSI 動量策略，在 BTC/USDT 1h 週期可以達到穩定正報酬，勝率 >= 55%。

## Domain
quantitative trading strategy

## Plugin
quant_alpha

## Performance Thresholds
- win_rate: 0.55
- max_drawdown: 0.20
- alpha_ratio: 1.0

## Universe
- instruments: BTC/USDT
- exchange: binance
- timeframe: 1h
- train_start: 2022-01-01
- train_end: 2023-06-30
- test_start: 2023-07-01
- test_end: 2024-01-01

## Entry Signal
RSI 從超賣區（< 30）回升至 35 以上時做多

## Exit Signal
RSI 上升至 65 以上時平倉，或觸及 stop_loss_pct 5% 時停損

## Notes
優先考慮低頻率交易策略，避免過度優化。
```

---

## Step 5：上傳 spec.md 到 Planka 卡片

1. 在 Planka 點開 `qa-test-rsi-001` 卡片
2. 找到 **Attachments** 區塊（卡片右側或底部）
3. 點擊 **Add Attachment** → 選擇剛才建立的 `spec.md` 檔案
4. 確認附件名稱顯示為 **`spec.md`**（名稱必須完全正確）

**檢查點 4：** ✅ 附件 `spec.md` 成功出現在卡片中

---

## Step 6：觸發 Spec 審查

將卡片從 **Planning** 欄拖曳到 **Spec Pending Review** 欄：

1. 點住卡片不放
2. 拖曳到 **Spec Pending Review** 欄位
3. 放開，確認卡片移入該欄

系統會自動啟動 spec 審查流程。

**等待時間：** 30 秒 ～ 2 分鐘

在等待期間可以觀察：

```bash
# 即時查看 engine log（另開終端機執行）
docker logs -f agentic-framework-api
```

尋找以下關鍵字確認流程已啟動：

```
spec_review | Starting spec review for project qa-test-rsi-001
```

**檢查點 5：** ✅ 卡片移入 Spec Pending Review，engine log 顯示審查已啟動

---

## Step 7：確認 Spec 審查通過，進入 Verify

等待卡片自動移動：

- **審查通過** → 卡片移至 **Verify** 欄（約 1～2 分鐘）
- **審查未通過** → 卡片移回 **Planning** 欄（查看卡片留言了解原因，修正後重回 Step 5）

**驗證步驟：**

1. 確認卡片出現在 **Verify** 欄
2. 點開卡片，查看 **Comments** 區，確認有 AI 的審查通過留言，例如：
   ```
   ✅ Spec review PASS — spec is complete and executable.
   ```
3. 確認 **Attachments** 區有一個新附件（AI 補全後的 spec），檔名類似 `spec_reviewed.md`

**檢查點 6：** ✅ 卡片在 Verify，留言顯示審查 PASS，有補全後的 spec 附件

---

## Step 8：監看研究循環執行（Verify 階段）

卡片在 **Verify** 期間，系統自動執行以下循環（最多 3 次）：

```
plan → implement → [HITL plan approval] → test → analyze
    ↕ FAIL: revise → plan（下一次 attempt）
    ↓ PASS: record_metrics → summarize → [Review 或繼續]
```

### 8a. 確認 HITL Plan Approval 被觸發

每個 loop 的 `implement` 階段，系統會暫停等待計畫審核。觀察 engine log：

```bash
docker logs -f agentic-framework-api
```

尋找：
```
implement | interrupt: plan approval required for qa-test-rsi-001
```

透過 API 批准計畫（approve），讓流程繼續：

```bash
curl -s -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "qa-test-rsi-001",
    "decision": {"action": "approve"}
  }'
```

**期望回應：** `{"status": "resumed"}`

> 每個 loop 都需要 approve 一次（max_loops=3，共最多 approve 3 次）。
> 如果中途 FAIL，revise 後會回到下一次 implement，再次需要 approve。

### 8b. 觀察每個 Loop 的結果

每次 `analyze` 執行後，查看卡片留言，會出現類似：

```
Loop 1 — FAIL
  win_rate: 0.48 (target: 0.55) ❌
  alpha_ratio: 0.92 (target: 1.0) ❌
  max_drawdown: 0.15 ✅
  → Revising: tighten entry threshold to 0.25, shorten lookback to 10
```

或：

```
Loop 2 — PASS
  win_rate: 0.57 ✅
  alpha_ratio: 1.05 ✅
  max_drawdown: 0.14 ✅
```

### 8c. 確認每個 Loop 產生 md 附件

PASS loop 完成後，卡片 Attachments 區應新增一個 per-loop 報告：

| Loop 結果 | 期望附件名稱格式 |
|-----------|----------------|
| PASS（loop 1） | `v1_researchsummary_YYYYMMDDHHMM.md` |
| PASS（loop 2） | `v2_researchsummary_YYYYMMDDHHMM.md` |
| PASS（loop 3） | `v3_researchsummary_YYYYMMDDHHMM.md` |

> FAIL 的 loop 不產生 per-loop md 附件，但會記錄在資料庫中。

**檢查點 7：** ✅ 每個 PASS loop 後，卡片新增對應的 `vN_researchsummary_*.md` 附件

---

## Step 9：確認 max_loops 後卡片移至 Review

3 次 analyze 完成後（無論 PASS/FAIL/EXHAUSTED），系統會：

1. 將卡片從 **Verify** 移至 **Review**
2. 在卡片留言貼上循環摘要報告

**驗證步驟：**

1. 確認卡片出現在 **Review** 欄
2. 點開卡片，查看留言，確認有完整的研究摘要，包含：
   - 每個 loop 的指標結果（win_rate、alpha_ratio、max_drawdown）
   - 每個 loop 的 PASS/FAIL 結論

**檢查點 8：** ✅ 卡片在 Review，留言包含完整的多輪循環摘要

---

## Step 10：執行 Terminate 決策，前往 Done

透過 API 發送 terminate 指令：

```bash
curl -s -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "qa-test-rsi-001",
    "decision": {"action": "terminate"}
  }' | python -m json.tool
```

**期望回應：** `{"status": "resumed"}`

等待約 30～60 秒，確認卡片自動移至 **Done** 欄。

**檢查點 9：** ✅ terminate 指令成功，卡片移至 Done

---

## Step 11：驗證最終輸出物（Done 狀態）

### 11a. 確認最終 summary md 附件

卡片 Attachments 區應有一份跨 loop 的總結報告：

**期望附件名稱格式：** `v1_vN_researchsummary_YYYYMMDDHHMM.md`
（例如：`v1_v3_researchsummary_202603241130.md`）

點開這個附件，確認內容包含：
- [ ] 研究標題與假設
- [ ] 每個 loop 的指標摘要表格
- [ ] 最佳 loop 的策略參數
- [ ] 整體結論

### 11b. 確認 DB 中的 loop_metrics 記錄

```bash
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "SELECT loop_index, result, reason FROM loop_metrics WHERE project_id = 'qa-test-rsi-001' ORDER BY loop_index;"
```

**期望輸出（3 次 loop 的完整記錄）：**

```
 loop_index | result |           reason
------------+--------+-----------------------------
          1 | FAIL   | win_rate below threshold ...
          2 | PASS   | All criteria met ...
          3 | PASS   | All criteria met ...
(3 rows)
```

> `loop_index` 應為 1、2、3（1-based），每個值唯一，不重複。

### 11c. 確認 MLflow 實驗記錄

1. 開啟 http://localhost:5000
2. 找到 experiment 名稱含 `qa-test-rsi-001` 的記錄
3. 確認有對應的 runs，每個 run 包含 `win_rate`、`alpha_ratio`、`max_drawdown` 等指標

**檢查點 10：** ✅ 最終 summary 附件存在，DB 有 3 筆 loop_metrics，MLflow 有實驗記錄

---

## Step 12：確認卡片最終狀態

在 Planka 點開 **Done** 欄的卡片，確認：

- [ ] 卡片在 **Done** 欄
- [ ] 卡片留言有最終研究摘要
- [ ] Attachments 包含：
  - [ ] 原始 `spec.md`
  - [ ] AI 補全的 spec（`spec_reviewed.md` 或類似名稱）
  - [ ] 每個 PASS loop 的 `vN_researchsummary_*.md`
  - [ ] 最終總結 `v1_vN_researchsummary_*.md`

**檢查點 11：** ✅ 所有附件齊全，卡片狀態為 Done

---

## 測試結果彙整

| # | 檢查點 | 預期結果 | 實際結果 | Pass/Fail |
|---|--------|----------|----------|-----------|
| 1 | Planka 登入 | 看到 6 個欄位 | | |
| 2 | 建立專案 API | status: created | | |
| 3 | Planning 卡片 | max_loops = 3 | | |
| 4 | spec.md 附件 | 附件名稱正確 | | |
| 5 | Spec Pending Review | engine log 有審查記錄 | | |
| 6 | Verify | 審查 PASS，有補全 spec | | |
| 7 | Per-loop md | 每個 PASS loop 有附件 | | |
| 8 | Review | 卡片移至 Review，有摘要 | | |
| 9 | Terminate | 卡片移至 Done | | |
| 10 | 最終輸出 | summary 附件 + DB 記錄 + MLflow | | |
| 11 | 卡片最終狀態 | Done，所有附件齊全 | | |

---

## 常見問題排查

### 問題：卡片停在 Spec Pending Review 超過 5 分鐘

```bash
# 查看 engine log 是否有錯誤
docker logs --tail=50 agentic-framework-api

# 確認 webhook 有收到事件
docker logs --tail=20 agentic-planka | grep webhook
```

可能原因：
- Planka Webhook 未設定，請確認 `http://langgraph-engine:8000/planka-webhook` 已在 Planka Admin → Webhooks 設定
- LLM 認證失敗（`claude-cli` token 過期），執行 `docker exec -it agentic-framework-api claude auth login`

### 問題：HITL approve 後流程沒有繼續

```bash
# 確認 resume endpoint 是否正常
curl -s http://localhost:7001/health

# 確認 project_id 拼寫正確
curl -s http://localhost:7001/projects/qa-test-rsi-001/status
```

### 問題：3 次 loop 後卡片沒有移至 Review

確認 `attempt_index` 是否有正確累加：

```bash
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "SELECT * FROM loop_metrics WHERE project_id = 'qa-test-rsi-001';"
```

若只有 1 筆記錄，表示 `analyze` wrapper 的 `attempt_index` 沒有正確更新。

### 問題：最終 summary 附件沒有出現

確認 `terminate_summarize_node` 有執行：

```bash
docker logs agentic-framework-api | grep "terminate_summarize\|final_summary"
```

---

## 附錄A：清除測試資料

重新測試前，執行以下步驟清除舊資料：

```bash
# 1. 刪除 DB 中的測試資料
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "DELETE FROM loop_metrics WHERE project_id = 'qa-test-rsi-001';"

# 2. 刪除 LangGraph checkpoint（若有）
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "DELETE FROM checkpoints WHERE thread_id = 'qa-test-rsi-001';"

# 3. 在 Planka 手動刪除測試卡片
# Planka → 找到 qa-test-rsi-001 卡片 → 右上角 ... → Delete Card
```

---

## 附錄B：只測試 API（不透過 Planka 操作）

如果環境中 Planka 尚未設定，可以純粹透過 API 驗證核心邏輯：

```bash
# 1. 建立專案
curl -s -X POST http://localhost:7001/projects \
  -H "Content-Type: application/json" \
  -d '{"project_id": "qa-api-test-001", "plugin": "quant_alpha", "max_loops": 2}'

# 2. 查詢狀態
curl -s http://localhost:7001/projects/qa-api-test-001/status

# 3. Approve plan（每個 loop 的 implement 節點後）
curl -s -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{"project_id": "qa-api-test-001", "decision": {"action": "approve"}}'

# 4. Terminate
curl -s -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{"project_id": "qa-api-test-001", "decision": {"action": "terminate"}}'

# 5. 確認 DB 記錄
docker exec -it agentic-postgres \
  psql -U agentic-postgres-user -d agentic-research \
  -c "SELECT loop_index, result FROM loop_metrics WHERE project_id = 'qa-api-test-001';"
```
