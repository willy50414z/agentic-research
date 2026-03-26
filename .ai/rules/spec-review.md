# Spec Review Rules

These rules apply when reviewing a research specification (`spec.md`).

## 1. Domain Identification

識別研究領域（例：Quantitative Trading Strategy、NLP Classification、Time Series Forecasting）。
若無法判定領域，這是唯一需要立即停下來問 user 的情況。

## 2. 填寫已知項目

- 將 user 明確寫出的內容完整保留，原文呈現，不改變意圖。
- 凡是有明確答案的欄位，直接填入。

## 3. 合理假設（可選項目）

對於可從領域知識推斷的項目：
- 套用該領域的最佳實務與保守預設。
- 在 `reviewed_spec.md` 的對應欄位填入推斷值。
- 在 `## 假設說明` 章節條列每個推斷，格式：`- **欄位名稱**：假設值 — 理由`。
- 禁止使用模糊詞：「適當」、「最佳化」、「TBD」、「視情況而定」。

## 4. 問題閾值（必須問 user 的情況）

**只有以下情況才列為問題**，其餘一律推斷並說明：
1. 無法判定研究領域
2. 需求互相矛盾且無法化解
3. 缺少無法合理推斷的關鍵商業決策（例：需要實盤部署但未指定交易所）

將問題列在 `reviewed_spec.md` 最後的 `## 待釐清問題` 章節，每項說明為何無法推斷。

## 5. `reviewed_spec.md` 結構

```
# <策略 / 研究名稱>

## 對需求的理解
<2–4 句：你理解 user 想達成什麼>

## 研究領域
<識別到的領域>

## [各研究要件章節，依領域展開]
<填入已知 + 推斷值；推斷值加註 *(假設)*>

## 假設說明
- **欄位**：值 — 理由

## 待釐清問題
（若無則省略此章節）
- <問題 1>：<為何無法推斷>
```

## 6. 量化交易策略必填欄位

若領域為 Quantitative Trading Strategy，`reviewed_spec.md` 必須包含：

- 市場論點
- 交易範圍：標的、資產類別、交易所、方向、時間週期、交易時段
- 資料規格：資料來源、必要欄位、訓練期間、驗證期間、測試期間、清理規則
- 進場訊號（精確條件：指標數值、交叉、閾值）
- 出場訊號（精確條件 + 停損）
- 倉位 Sizing 與風險規則
- 執行假設：訂單類型、滑價、手續費、延遲、再平衡頻率
- 績效門檻：min win rate、max drawdown、min alpha ratio、profit factor (IS/OOS)
- Plugin：`quant_alpha`（固定值，無論 spec 寫什麼都統一更正為此值）

## 7. 輸出檔案規範

寫入 `{OUTPUT_DIR}` 的檔案：

| 檔案 | 條件 | 內容 |
|------|------|------|
| `reviewed_spec_primary.md`（Primary）或 `reviewed_spec_secondary.md`（Secondary） | 每次都寫 | 完整審查後的規格文件 |
| `status_pass.txt` | 規格完整，無待釐清問題 | 空檔（或寫 `PASS`） |
| `status_need_update.txt` | 有待釐清問題 | 每行一個問題，與 `## 待釐清問題` 內容一致 |

`status_pass.txt` 與 `status_need_update.txt` 二擇一，不可同時存在。
