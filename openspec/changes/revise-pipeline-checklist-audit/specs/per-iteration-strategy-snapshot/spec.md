## ADDED Requirements

### Requirement: 每輪 strategy `.py` 採 staging path 生命週期

`_run_revise` 在 Stage D 期間 SHALL 將新 strategy `.py` 寫入 staging 路徑 `artifacts/.staging/v{N}/candidate.py`（N = `analyze_attempt`），而非直接寫入正式目錄。

僅當 audit `overall: APPROVED` 時，orchestrator SHALL 將 staging 中的 candidate.py promote 到正式路徑 `artifacts/strategies/v{N}/{StrategyName}.py`，並更新 `implementation_plan.strategy_file` 指向該正式路徑。

Promote 操作 SHALL 為原子（atomic move 或 atomic copy + rename），避免 partial write 造成 freqtrade 讀到半成品。

第 0 輪（baseline）由 `plan_step` 直接寫入 `artifacts/strategies/v0/{StrategyName}.py`（無 staging，因 baseline 無 audit）。

#### Scenario: Audit 通過後 promote
- **WHEN** Stage D 寫入 `artifacts/.staging/v1/candidate.py`，Stage E audit `overall: APPROVED`
- **THEN** orchestrator promote 至 `artifacts/strategies/v1/{StrategyName}.py`、`plan.strategy_file` 對應更新

#### Scenario: Audit 未通過不 promote
- **WHEN** revise 流程任何階段 TERMINATE 或所有 retry 用罄
- **THEN** `artifacts/.staging/v{N}/` 保留供 forensics、`artifacts/strategies/v{N}/` 不存在；`plan.strategy_file` 維持指向上一輪（v{N-1}）的路徑；後續 `_real_implement` 與 freqtrade `--strategy-path` 指向 v{N-1} 而非 staging 目錄

#### Scenario: Promote 為原子操作
- **WHEN** orchestrator 執行 promote 期間發生例外（IO error、disk full 等）
- **THEN** `artifacts/strategies/v{N}/` 要嘛完整存在要嘛不存在，不得出現 partial 檔案；若 partial 出現 SHALL 視為 promote 失敗、orchestrator TERMINATE 該輪、`plan.strategy_file` 不更新

#### Scenario: Baseline 直接寫正式路徑
- **WHEN** plan_step 完成、`analyze_attempt = 0`
- **THEN** `.py` 直接寫入 `artifacts/strategies/v0/{StrategyName}.py`、無 staging 階段、`plan.strategy_file` 對應更新

---

### Requirement: 每輪 freqtrade 用隔離目錄

`_real_implement` 與 freqtrade backtest 的 `--strategy-path` SHALL 指向當輪 `artifacts/strategies/v{N}/`（而非 staging）。

該目錄保證僅含當輪 `.py` 一份檔案，避免 freqtrade 掃描時抓到舊輪 class。

#### Scenario: freqtrade 用隔離目錄
- **WHEN** `_real_implement` 呼叫 freqtrade backtest，`plan.strategy_file = artifacts/strategies/v1/Foo.py`
- **THEN** freqtrade CLI 收到 `--strategy-path artifacts/strategies/v1/`；目錄內僅含當輪 `.py`

#### Scenario: TERMINATE 後仍跑得起來（用上一輪）
- **WHEN** v2 revise TERMINATE、`plan.strategy_file` 指向 `artifacts/strategies/v1/Foo.py`
- **THEN** 後續若有觸發 implement（理論上不該觸發），freqtrade `--strategy-path` 指向 `v1/`，不會誤入 `.staging/v2/`

---

### Requirement: 每輪輸出 strategy spec 快照（SoT 鎖定為 `.py`）

`_run_revise` 在 promote 完成後 SHALL 產出 `v{N}_strategy_spec.md`；產出規則：

**SoT 紀律**：

- 所有結構性內容（策略類別名、timeframe、stoploss、minimal_roi、所有 hyperopt parameter 的 default 值、進場/出場條件）SHALL **僅由 deterministic AST 解析從通過 audit 的 `.py` 萃取**
- 進場/出場條件 SHALL 以 AST 解析 `populate_entry_trend` / `populate_exit_trend` 的條件結構後輸出（例如 `dataframe['rsi'] >= 35 AND dataframe['close'] > dataframe['ema20']` 這類人類可讀但結構化的描述）
- LLM 在快照產生過程中**僅可補充非結構性說明**：本輪修訂動機（取自 intent.md）、與前輪 delta 的人類可讀描述（取自 checklist.yaml 的 rationale 欄位）
- LLM SHALL NOT 生成、推測、修改任何參數值或進出場條件數值
- 若 deterministic 解析某欄位失敗（AST 無法處理該結構），對應欄位 SHALL 寫 `<unparseable>`、log warning，**禁止 LLM 推測填入**

**Delta 計算（依 item type 分流，避免實作分裂）**：

`v{N}_strategy_spec.md` 的 delta 區段 SHALL 對 checklist 中每個 item 依 type 走不同來源：

- **`type: param` items**：delta 直接由 checklist 的 `from` → `to` 衍生，輸出 `<target.name>: <from> → <to>`（machine-readable）。`from` 與 `to` 均為 deterministic 已確認的值
- **`type: logic` items**：因 logic items 沒有 `from`/`to` 欄位，delta SHALL 由下列三個來源組合產生**文字化 delta**：
  - `target.function`：指出受影響的 function
  - `expected_signals` 列表：作為「新增 / 修改的行為」描述
  - `forbidden_signals` 列表（如非空）：作為「明確移除的行為」描述
  - `rationale`：作為人類可讀的修訂理由（**僅 logic items delta 可顯示 rationale；audit 階段仍嚴守不傳給 LLM3 的紀律**）
- **僅 deterministic 萃取的補充驗證**：對 `type: param` items，`v{N}_strategy_spec.md` 中的 delta 顯示值 SHALL 與 `.py` AST 萃取出的當前值一致；不一致時 raise error（這是又一道防線，避免 checklist 與實際 .py 默默背離）

delta 區段每個 item 的標題 SHALL 標註資料來源（`(source: checklist.param)` 或 `(source: checklist.logic + AST)`）以利日後審閱。

**baseline（v0）特殊規則**：

- 由 `plan_step` 而非 `_run_revise` 產出
- SoT 紀律相同：結構性內容僅從 `artifacts/strategies/v0/{StrategyName}.py` AST 萃取
- 無 delta 區段（無前輪可比）

**上傳**：

- 完成後 SHALL 上傳至 Planka 卡片（`upload_spec_attachment`）
- 上傳失敗 SHALL 僅 log warning、本地檔案保留、不中斷 workflow

#### Scenario: 結構性內容只從 .py 萃取
- **WHEN** 產生 `v1_strategy_spec.md`，`.py` 含 `IntParameter(low=10, high=20, default=15, ...)` for `rsi_period`
- **THEN** 快照中 `rsi_period: 15` 由 AST 萃取；LLM 不得改寫此值

#### Scenario: 進場條件由 AST 萃取
- **WHEN** `.py` 的 `populate_entry_trend` 含 `dataframe.loc[(rsi >= 35) & (close > ema20), "enter_long"] = 1`
- **THEN** 快照中 entry conditions 區段顯示 `rsi >= 35 AND close > ema20`，由 AST 結構化輸出

#### Scenario: AST 解析失敗
- **WHEN** `.py` 中某欄位採非標準結構，deterministic 解析失敗
- **THEN** 快照中該欄位寫 `<unparseable>`，log warning「strategy_spec deterministic extraction failed for field X」；LLM 不得推測填入

#### Scenario: LLM 補充說明限制
- **WHEN** 快照產生過程，LLM 拿到 intent.md + checklist.yaml + 已萃取的結構化資料
- **THEN** LLM 只能輸出「修訂動機摘要」與「delta 描述」兩個自然語言段落；orchestrator 將 LLM 輸出與 deterministic 萃取結果合併寫入 markdown，LLM 段落 SHALL 以章節標題明確區隔（例如 `## 修訂摘要（LLM 補充說明）`）

#### Scenario: param item 的 delta
- **WHEN** checklist 含 `{id: M1, type: param, target: {kind: class_attr, name: stoploss}, from: -0.05, to: -0.03}`
- **THEN** `v{N}_strategy_spec.md` delta 區段顯示 `M1 (source: checklist.param): stoploss: -0.05 → -0.03`；同時 AST 萃取 `.py` 中當前 stoploss 值並比對為 `-0.03`，若不一致 SHALL raise error

#### Scenario: logic item 的 delta（無 from/to）
- **WHEN** checklist 含 `{id: M3, type: logic, target: {function: populate_indicators}, expected_signals: ["呼叫 ta.EMA timeperiod=20"], forbidden_signals: [], rationale: "加入趨勢過濾基礎"}`
- **THEN** `v{N}_strategy_spec.md` delta 區段顯示：

  ```
  M3 (source: checklist.logic + AST)
    function: populate_indicators
    新增/修改行為: 呼叫 ta.EMA timeperiod=20
    修訂理由: 加入趨勢過濾基礎
  ```

#### Scenario: logic item 含 forbidden_signals
- **WHEN** checklist 含 `{id: M6, type: logic, target: {function: populate_indicators}, expected_signals: [...], forbidden_signals: ["ta.ATR 不再被呼叫"], rationale: "移除 ATR 機制"}`
- **THEN** delta 區段除「新增/修改行為」外另列「明確移除的行為: ta.ATR 不再被呼叫」

#### Scenario: baseline 快照無 delta
- **WHEN** plan_step 完成、`analyze_attempt = 0`
- **THEN** `v0_strategy_spec.md` 產出，含完整參數值（從 `.py` AST 萃取）；無 delta 區段

#### Scenario: 上傳失敗不中斷流程
- **WHEN** `upload_spec_attachment` 拋出例外
- **THEN** 系統 SHALL log warning，本地檔案保留，workflow 繼續
