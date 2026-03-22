# 使用者操作指南 — Agentic Research

## 概覽

Agentic Research 是一個 AI 驅動的自動化研究工作流程系統。你只需要寫一份研究規格（spec），系統就會自動循環執行 **規劃 → 實作 → 測試 → 分析**，並在每隔幾輪成功後暫停等待你的決策。

**主要介面：** Planka 看板（`http://localhost:7002`）

---

## 工作流程總覽

```
你寫 spec.md
     ↓
系統 AI 審查 spec（Spec Pending Review）
     ↓
研究循環自動執行（Verify）
 plan → implement → test → analyze
              ↕ FAIL
           revise（自動重試）
     ↓ 每 N 輪 PASS
暫停等待你的決策（Review）
  ├─ continue  → 繼續下一輪
  ├─ replan    → 給 AI 新方向，重新規劃
  └─ terminate → 結束研究，輸出報告
     ↓
Done（研究完成）
```

---

## 第一步：登入 Planka

1. 瀏覽器開啟 `http://localhost:7002`
2. 使用管理員提供的帳號密碼登入
3. 進入 **Agentic Research** Board

你會看到 6 個欄位：

| 欄位 | 說明 |
|------|------|
| **Planning** | 新建立的專案，等待你撰寫 spec |
| **Spec Pending Review** | AI 正在審查你的 spec |
| **Verify** | 研究循環自動執行中 |
| **Review** | 需要你做決策（每 N 輪觸發） |
| **Done** | 研究完成 |
| **Failed** | 發生錯誤，需要人工介入 |

---

## 第二步：建立新專案

由 RD 或管理員透過 API 建立專案後，你的 Board 上會出現一張新的卡片，位於 **Planning** 欄。

卡片說明中會包含 `thread_id: <project_id>`，這是系統識別此專案的唯一 ID，**請勿修改**。

---

## 第三步：撰寫 Spec

Spec 是告訴 AI 你想研究什麼的規格文件。格式為 Markdown。

### Spec 基本結構

```markdown
# Research Spec

## Hypothesis
（描述你的研究假設或策略想法）
例：使用 RSI 動量策略，在 BTC/USDT 上可以達到穩定的正報酬

## Domain
（研究領域）
例：quantitative trading strategy

## Plugin
（使用哪個插件）
例：quant_alpha

## Performance Thresholds
（定義何謂「成功」的指標門檻）
- win_rate: 0.55           # 勝率需 >= 55%
- max_drawdown: 0.20       # 最大回撤需 <= 20%
- alpha_ratio: 1.0         # alpha/beta 需 >= 1.0
- is_profit_factor: 1.2    # 樣本內 profit factor
- oos_profit_factor: 1.1   # 樣本外 profit factor

## Universe
（研究範圍）
- instruments: BTC/USDT
- exchange: binance
- timeframe: 1h
- train_start: 2022-01-01
- train_end: 2023-06-30
- test_start: 2023-07-01
- test_end: 2024-01-01

## Entry Signal
（進場條件描述）
例：RSI 從超賣區（< 30）回升至 35 以上時做多

## Exit Signal
（出場條件描述）
例：RSI 上升至 65 以上時平倉

## Notes
（補充說明，給 AI 的額外提示）
例：優先考慮低頻率交易策略，避免過度優化
```

### 上傳 Spec 到 Planka 卡片

1. 點開你的專案卡片
2. 在 **Attachments** 區塊，上傳你的 spec 檔案，**檔名必須是 `spec.md`**
3. 確認附件已成功上傳

---

## 第四步：提交 Spec 審查

將卡片從 **Planning** 拖曳到 **Spec Pending Review**。

系統會自動：
1. 下載你的 `spec.md`
2. 由 Primary AI 重寫並補全 spec 細節
3. 由 Secondary AI 審查執行性與一致性
4. 若 spec 沒問題 → 卡片移至 **Verify**，研究循環開始
5. 若 spec 有問題 → 卡片移回 **Planning**，AI 會在卡片留言說明需要補充哪些內容

> 審查過程通常需要 30 秒到 2 分鐘。可以在卡片的留言區看到 AI 的處理進度。

### Spec 被退回時

1. 閱讀 AI 在卡片留言的說明
2. 下載最新的附件（AI 會上傳修改過的版本供你參考）
3. 根據建議修改你的 `spec.md`
4. 重新上傳修改後的 `spec.md` 到卡片
5. 再次將卡片拖到 **Spec Pending Review**

---

## 第五步：監看研究進度

卡片在 **Verify** 欄時，代表研究循環正在自動執行。

你可以在卡片的**留言區**看到每個步驟的執行摘要，包括：
- AI 規劃的策略內容
- 測試指標結果（win_rate、max_drawdown 等）
- 分析結論（PASS / FAIL / TERMINATE）
- 自動重試的修正方向

**不需要做任何操作**，等待卡片自動移到 **Review** 即可。

---

## 第六步：Loop Review（關鍵決策）

每隔 N 輪成功（PASS）後，系統會暫停並將卡片移至 **Review** 欄，等待你的決策。

此時卡片留言中會有本輪的績效摘要報告。

### 決策方式：拖曳卡片

| 你的操作 | 系統行為 |
|----------|----------|
| 拖到 **Verify** | continue：以相同方向繼續下一輪研究 |
| 拖到 **Done** | terminate：結束研究，產出最終報告 |
| 拖到 **Failed** | terminate（異常）：標記為失敗並結束 |

### 決策方式：API（進階，可附加備註）

```bash
# 繼續研究
curl -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{"project_id":"<your-project-id>","decision":{"action":"continue"}}'

# 指定新方向重新規劃
curl -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{
    "project_id": "<your-project-id>",
    "decision": {
      "action": "replan",
      "notes": "嘗試更長的回看週期，並加入成交量過濾條件"
    }
  }'

# 終止研究
curl -X POST http://localhost:7001/resume \
  -H "Content-Type: application/json" \
  -d '{"project_id":"<your-project-id>","decision":{"action":"terminate"}}'
```

`<your-project-id>` 可以從卡片說明中的 `thread_id: <project_id>` 找到。

---

## 第七步：取得研究結果

當卡片移至 **Done** 時，研究已完成。

- **卡片留言**：最終摘要報告（Markdown 格式）
- **Planka 附件**：如有產出報告檔案，會附在卡片上
- **MinIO**：完整的 artifacts 存放在 `research-artifacts` bucket（需聯絡 RD 協助下載）

---

## 常見問題

### 卡片一直停在 Spec Pending Review 沒有動
- 等待 2 分鐘後，如果還沒反應，可以嘗試再次拖曳卡片到 Spec Pending Review 觸發重試
- 聯絡 RD 查看 `langgraph-engine` 的 log

### AI 審查 spec 說缺少必要欄位
spec.md 中必須包含以下欄位，否則審查不會通過：
- `Plugin`（使用哪個插件）
- `Performance Thresholds`（至少要有 win_rate 或 accuracy 等量化門檻）
- `Universe`（研究的資料範圍）

### 研究循環一直 FAIL 沒有 PASS
- 查看卡片留言，AI 會說明 FAIL 的原因和修正方向
- 如果連續失敗超過預期，可以在 Review 時使用 `replan` 動作並附上新的方向說明

### 卡片移到 Failed
- 代表系統遇到技術性錯誤
- 查看卡片留言中的錯誤訊息
- 聯絡 RD 查看 `langgraph-engine` log 進行排查

### 忘記 project_id 是什麼
點開 Planka 卡片，在說明（Description）中找 `thread_id: <project_id>` 這行文字。

---

## 看板狀態速查

```
Planning          → 等待你上傳 spec.md
Spec Pending Review → AI 審查中（1~2 分鐘）
Verify            → 研究自動執行中（無需操作）
Review            → 需要你做決策 ⭐
Done              → 完成，可取結果
Failed            → 發生錯誤，聯絡 RD
```
