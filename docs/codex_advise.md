# Agentic Research SOP 評價與建議

## 整體評價

這份 SOP 的完成度高，已經不只是概念計畫，而是接近可交付的操作規格。它的強項很明確：把輸入、流程節點、產出檔案、人工介入點、驗證方式、故障排查都串起來了，特別是 file-based 輸出協定、雙 LLM 分工、Planka 狀態流轉、以及 artifacts/DB 的可追蹤性，這些都讓系統更容易 debug 與重現。作為專案計畫書，它的工程可執行性比一般願景型文件強很多。

但如果從「專案計畫」角度看，現在這份文件最大的問題不是內容不夠，而是現況、規格、未來規劃混在一起。這會讓開發、QA、未來維運人員在判讀時出現落差，尤其是在 spec review 的狀態語義、HITL 是否真的生效、以及 Freqtrade 整合到底是現有能力還是 roadmap 這幾件事上。

## 主要問題

### 1. 規格審查狀態語義不一致

流程總覽中寫的是 `PASS -> Verify；FAIL -> Planning`，但實際 spec review 規則是 `PASS / NEED_UPDATE`，而且 `status_need_update.txt` 也是同一套語義。這會讓 API 狀態、卡片留言、QA 驗證與錯誤處理邏輯產生歧義。

### 2. 文件定位混合了目前 SOP 與未來設計

前面宣稱是完整功能規格，但後面多處又標示 HITL 停用、final summary 計畫中、Freqtrade 整合計畫中。這種寫法對開發者還能理解，但對 QA 或新進成員很容易誤判哪些流程今天真的能跑。

### 3. 關鍵依賴被寫死，系統韌性不足

Spec Review 強制要求 `LLM_CHAIN` 至少兩個 provider，且角色綁定 `participants[0]` 與 `participants[-1]`。這種設計在品質上合理，但在實務上會讓部署、故障切換、成本控制都變差；任一 provider 不可用時，整條 review 流就可能卡住。

### 4. 成功定義偏向回測指標，缺少系統層級 KPI

現在文件對 loop 的 PASS/FAIL 很清楚，但對專案成功沒有明確量化，例如平均 review latency、單 loop 完成時間、失敗重試率、人工介入率、附件上傳成功率。這會讓你很難在上線後判斷系統本身是否有變好。

## 建議

### 1. 把文件拆成兩層

- 一份是「Runbook / Current SOP」，只保留今天真的可執行的流程。
- 一份是「Roadmap / Planned Design」，集中放 HITL、Freqtrade 整合、final summary 等未完成能力。

這會立刻提升可讀性與 QA 可測性。

### 2. 統一狀態詞彙

Spec Review 階段建議全文件只用一套：

- `PASS`
- `NEED_UPDATE`
- `ABORT`

研究循環再保留：

- `PASS`
- `FAIL`
- `TERMINATE`

這樣狀態語義會乾淨很多。

### 3. 為每個階段補入口條件與出口條件

每個階段建議補一個簡表，至少包含：

- 入口條件
- 出口條件
- 失敗回退方式

你現在有大量描述，但還缺明確 gate。每階段若補這三欄，實作、測試、維運都會更穩。

### 4. 增加系統 KPI

至少補這些：

- `spec review 完成時間`
- `research loop 平均耗時`
- `LLM 失敗 fallback 比率`
- `人工介入率`
- `Planka webhook 成功率`
- `附件上傳成功率`

這些比單純策略績效更能衡量平台成熟度。

### 5. 為雙 LLM 設計降級模式

例如：

- 雙 provider 正常時走 Author + Synthesizer
- 單 provider 故障時切成 single-review + stricter validation
- 兩者都失敗時直接回 `ABORT` 並附標準化錯誤碼

這樣系統不會因為一個外部依賴暫時失效就整段停擺。

### 6. 把「計畫中」功能改成更工程化的格式

不要只用符號標記，建議補：

- `Status`
- `Owner`
- `Blocking dependency`
- `Target milestone`

這樣它才真正是專案計畫，而不只是說明文件。

## 值得保留的優點

這份計畫最值得保留的，是已經把流程狀態機和檔案契約定得很清楚，這是 agentic workflow 成功的核心。尤其以下三塊最有工程價值：

- file-based 協定
- 產出檔案清單
- 故障排查章節

這三塊讓系統具備可追蹤、可驗證、可維運的基礎，建議在後續重構時優先保留。
