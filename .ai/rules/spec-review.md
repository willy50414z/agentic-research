# Spec Review Rules

These rules apply when reviewing a research specification (`spec.md`).

## 1. Domain Identification

識別研究領域，例如 `Quantitative Trading Strategy`、`NLP Classification`、`Time Series Forecasting`。
若無法判定研究領域，這是唯一可以列為待釐清問題並要求 user 補充的最高優先事項。

## 2. 填寫已知項目

- 保留 user 已明確提供的內容，原意不得改寫。
- 凡是已有明確答案的欄位，直接填入對應章節。

## 3. 合理假設

對於可從領域知識、常見實務或保守預設合理推斷的項目：

- 直接補入 spec，不要先回問 user。
- 在 `## 假設說明` 中逐條列出「欄位、假設值、理由」。
- 禁止使用模糊詞，例如「適當」、「最佳化」、「TBD」、「視情況而定」。
- 不可只把缺漏欄位改寫成較長的模糊敘述；所有補全都必須具備可執行值。

## 4. 問題門檻

只有以下情況才可以列為待釐清問題，其餘一律先推斷並說明：

1. 無法判定研究領域。
2. 需求互相矛盾且無法化解。
3. 缺少無法合理推斷的關鍵商業決策或關鍵回測設定。

待釐清問題必須符合：

- 每題都是單一、可直接回答的問題。
- 不可把多個缺口打包成一題。
- 必須放在 `## 待釐清問題` 章節。
- `status_need_update.txt` 每一行都必須與 `## 待釐清問題` 逐字一致。

## 5. PASS / NEED_UPDATE 判定

- 若所有必填欄位已完整、可量化、可執行，且剩餘缺漏都可合理推斷，則判定為 PASS。
- 只有在存在「無法合理推斷的關鍵商業決策或關鍵回測設定」時，才判定為 NEED_UPDATE。

## 6. `reviewed_spec.md` 結構

```md
# <策略 / 研究名稱>

## 對需求的理解
<2-4 句，說明你理解 user 想達成什麼>

## 研究領域
<識別到的領域>

## [依領域展開的主體章節]
<填入已知內容與推斷值；推斷值加註 *(假設)*>

## 假設說明
- **欄位**：值 — 理由

## 待釐清問題
（若 PASS 則不得出現此章節）
- <問題 1>：<為何無法推斷>
```

補充要求：

- `## 對需求的理解`、`## 研究領域`、`## 假設說明` 為必備章節。
- 若為 PASS，禁止出現 `## 待釐清問題`。
- 若為 NEED_UPDATE，必須出現 `## 待釐清問題`。

## 7. 量化交易策略必填欄位

若領域為 `Quantitative Trading Strategy`，`reviewed_spec.md` 必須包含：

- 市場論點
- 交易範圍：標的、資產類別、交易所、方向、時間週期、交易時段
- 資料規格：資料來源、必要欄位、訓練期間、驗證期間、測試期間、清理規則
- 進場訊號：精確條件，例如指標數值、交叉、閾值、成交時點
- 出場訊號：精確條件與停損
- 倉位 Sizing 與風險規則
- 執行假設：訂單類型、滑價、手續費、延遲、再平衡頻率
- 績效門檻：min win rate、max drawdown、min alpha ratio、profit factor (IS/OOS)
- Plugin：固定為 `quant_alpha`

## 8. 補件重審原則

- Refine 路徑應逐題核對上一輪問題與 user 回覆，不得重問已回答且已足以落地的問題。
- 若 user 最新回答推翻舊假設，必須同步更新 spec 與 `## 假設說明`，移除失效假設。
- Refine 路徑應聚焦於「user 回答是否解掉前一輪問題」，不要擴張成整份 spec 的發散式重審，除非 user 的新回答引入新的內部矛盾。
- Synthesizer 是最終守門員，應優先驗證是否符合規則與 SOP，而不是為了文風做自由改寫。
- Synthesizer 只應在補齊缺漏、修正矛盾、或把模糊敘述改成可執行值時修改內容。

## 9. 輸出檔案規範

寫入 `{OUTPUT_DIR}` 的檔案：

| 檔案 | 條件 | 內容 |
|------|------|------|
| `reviewed_spec_initial.md`（initial）或 `reviewed_spec_final.md`（refine / synthesize） | 每次都寫 | 完整審查後的規格文件 |
| `status_pass.txt` | 規格完整，無待釐清問題 | 內容為 `PASS` |
| `status_need_update.txt` | 有待釐清問題 | 每行一個問題，與 `## 待釐清問題` 內容一致 |

`status_pass.txt` 與 `status_need_update.txt` 二擇一，不可同時存在。
