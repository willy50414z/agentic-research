## ADDED Requirements

### Requirement: checklist.yaml Schema 契約

`_run_revise` Stage C 產出的 `checklist.yaml` SHALL 符合下列 schema：

**頂層必填欄位**：

- `iteration` (int)：對應 `analyze_attempt`
- `based_on_intent` (string)：來源 intent.md 的相對路徑
- `created_by` (string)：固定為 `"llm2"`（產 checklist 的角色）
- `locked` (bool)：產出時 SHALL 為 `true`；subagent 與 audit 不得改寫
- `invariants` (list[string])：全域不變式 ID 清單，至少包含 `timeframe_unchanged`、`class_name_unchanged`、`order_types_four_keys`、`no_lookahead_pattern`
- `items` (list)：checklist 項目陣列，至少 1 項

**每個 item 必填欄位**：

- `id` (string)：唯一識別碼（建議 `M{n}` 格式）
- `type` (enum)：`param` | `logic`，二選一
- `rationale` (string)：人類可讀的修訂理由（**audit 階段 SHALL NOT 被傳入 LLM3**）

**`type: param` 額外必填**：

- `target.kind` (enum)：`class_attr` | `hyperopt_param` | `dict_value`
- `target.name` (string)：目標識別名稱
- `target.field` (string，僅 `hyperopt_param` 時)：`default` | `low` | `high` | `decimals`
- `target.path` (string，僅 `dict_value` 時)：點分路徑語法，例如 `minimal_roi."0"`
- `from`：修訂前的值（型別與 .py 中該欄位型別一致）
- `to`：修訂後的值（型別與 from 一致）

**`type: logic` 額外必填**：

- `target.function` (string)：目標 function 名稱（例如 `populate_indicators`）
- `expected_signals` (list[string])：至少 1 條；採行為描述語言，**不可指定變數名稱**或語法層細節
- `forbidden_signals` (list[string])：可空陣列；用於明確禁止的 pattern

**`type: logic` 可選欄位**：

- `depends_on` (list[string])：相依的 item id（純註記，audit 不執行 dep 邏輯）

#### Scenario: 合法 param item
- **WHEN** checklist 含 `{id: M1, type: param, target: {kind: class_attr, name: stoploss}, from: -0.05, to: -0.03, rationale: "..."}`
- **THEN** schema validation PASS

#### Scenario: 缺少必填欄位
- **WHEN** checklist 中某 item 缺 `from` 或 `to`
- **THEN** `parse_checklist` SHALL raise `ValidationError`，Stage C 視為失敗計入 checklist_retry

#### Scenario: locked=false 拒絕
- **WHEN** Stage C 產出的 yaml `locked: false`
- **THEN** orchestrator SHALL 拒絕該 checklist，視為 Stage C 失敗

---

### Requirement: completion_report.yaml Schema 契約

Subagent 在 Stage D 結束時 SHALL 產出 `completion_report.yaml`，與 checklist.yaml 同目錄，schema 如下：

**頂層必填欄位**：

- `iteration` (int)：對應 checklist.iteration
- `attempt` (int)：當輪 subagent_retry 計數，從 0 開始
- `items` (list)：每項對應 checklist 中一個 item
- `unimplementable_items` (list[string])：item id 清單；空陣列代表 subagent 認為 checklist 完全可實作

**每個 item 必填欄位**：

- `id` (string)：對應 checklist item id
- `completed` (bool)：subagent 自報是否完成
- `location` (string，僅 `completed: true`)：`"<filename>:<line>"` 格式，指向實際改動位置
- `blocking_reason` (string，僅 `completed: false` 且該 id 在 `unimplementable_items` 中)：說明為何 checklist 此項不可實作
- `note` (string，optional)：subagent 自由說明

**契約規則**：

- `unimplementable_items` 非空 → orchestrator SHALL 走 `UNIMPLEMENTABLE_CHECKLIST` 路徑（退回 Stage C）
- `unimplementable_items` 空 + 全部 `completed: true` → orchestrator SHALL 進入 Stage E audit
- `unimplementable_items` 空 + 任一 `completed: false` → orchestrator SHALL 視為 Stage D 異常退回（不退 Stage C），計入 `subagent_retry`

#### Scenario: 全部完成
- **WHEN** completion_report 中所有 items `completed: true` 且 `unimplementable_items: []`
- **THEN** 進入 Stage E audit

#### Scenario: 標記 unimplementable
- **WHEN** completion_report `unimplementable_items: [M3]` 且 M3 對應 item `completed: false, blocking_reason: "..."`
- **THEN** orchestrator 退回 Stage C，計入 `checklist_retry`

#### Scenario: 部分未完成但未標記 unimplementable
- **WHEN** 某 item `completed: false` 但 `unimplementable_items` 不含該 id
- **THEN** orchestrator 視為 Stage D 失敗，計入 `subagent_retry`，要求 subagent 重寫

---

### Requirement: audit_report.yaml Schema 與判定語意

`_run_revise` Stage E SHALL 產出 `audit_report.yaml`，內容 SHALL 符合下列 schema、verdict 計算規則、以及 dishonest 判定規則：

**頂層必填欄位**：

- `iteration` (int)
- `attempt` (int)：對應該次 subagent_retry
- `deterministic_results` (list)：對 invariants 與所有 `type: param` item 的逐項結果
- `llm3_results` (list)：對所有 `type: logic` item 的結果；若 deterministic 階段 fail，此欄位 SHALL 為空陣列（short-circuit）
- `overall` (enum)：`APPROVED` | `REJECTED`
- `reject_summary` (string，僅 `REJECTED`)：簡述失敗原因

**每個 result 必填欄位**：

- `id` (string)：對應 checklist item id 或 invariant 名稱
- `verdict` (enum)：`PASS` | `FAIL` | `INSUFFICIENT`
- `evidence` (string)：證據（line number、AST 節點、或 LLM3 摘要）
- `subagent_self_report_consistent` (bool | null)：**deterministic results 與 llm3 results 都 SHALL 填**；若該 result 可對應到 checklist item，則填 `true` 或 `false`；若該 result 無對應 checklist item（例如 invariant 或 `unauthorized_change` 特殊條目），則填 `null`

**Verdict 語意**：

- `PASS`：明確通過該項檢查
- `FAIL`：明確未通過；deterministic 階段 → mismatch；LLM3 階段 → expected_signals 至少一條未成立、或 forbidden_signals 至少一條出現
- `INSUFFICIENT`：**僅 LLM3 可使用**；意指 expected_signals 表述模糊到無法從 .py 判斷；orchestrator SHALL 視 INSUFFICIENT 為 FAIL，但回 Stage C 修 checklist 而非 Stage D 重寫 code（因為 checklist 表述本身有問題）

**Overall 計算規則**：

- 任一 deterministic result 為 FAIL → overall = REJECTED（且 llm3_results 為空）
- deterministic 全 PASS 但任一 LLM3 result 為 FAIL → overall = REJECTED
- deterministic 全 PASS 且 LLM3 至少一項 INSUFFICIENT 而其餘 PASS → overall = REJECTED，reject_summary 標註 `CHECKLIST_AMBIGUOUS`，orchestrator 退 Stage C（不計入 subagent_retry）
- deterministic 全 PASS 且 LLM3 全 PASS → overall = APPROVED

**Subagent dishonest 判定**：

dishonest 判定 SHALL 同時涵蓋 deterministic 與 LLM3 兩層的結果（**這是本次 change 最核心的反 self-affirmation 機制**——deterministic param/invariant mismatch 正是上次 bug 的特徵，這層必須被納入）：

對 deterministic_results 中每一項 result：

- 若 `verdict = FAIL` 且該 result 對應的 checklist item id 在 `completion_report.items` 中標 `completed: true` → `subagent_self_report_consistent: false`
- 若 result 對應的是 invariant（無 checklist item id 對應），則該 result 永遠 `subagent_self_report_consistent: null`（不適用 dishonest 判定，invariant 違反由其他 retry 機制處理）

對 llm3_results 中每一項 result：

- 若 `verdict = FAIL` 或 `verdict = INSUFFICIENT` 且對應 completion_report 該 item `completed: true` → `subagent_self_report_consistent: false`
- INSUFFICIENT 雖屬 checklist 表述問題，但 subagent 自報「完成」一個本身表述不清的 item 仍視為自我宣稱不一致

**TERMINATE 觸發**：

- 同一輪 audit attempt 中，只要 deterministic_results 與 llm3_results 合計出現至少一個 `subagent_self_report_consistent: false`，該輪 attempt 即記為「dishonest_attempt」
- 連續兩輪 attempt 均為 dishonest_attempt → orchestrator SHALL 立即 TERMINATE 該輪 revise（不再 retry，不論其他 counter 狀態）
- 「連續」的計數在 checklist 變更時歸零（新 checklist 下重新計算 dishonest_attempt 連續性）

#### Scenario: Deterministic FAIL short-circuit
- **WHEN** invariant `timeframe_unchanged` 偵測新 .py 的 timeframe 與舊 .py 不同
- **THEN** deterministic_results 含該項 FAIL，llm3_results 為空，overall = REJECTED；不發起 LLM3 call

#### Scenario: LLM3 INSUFFICIENT 退回 checklist
- **WHEN** LLM3 對 M3 回 INSUFFICIENT，理由「expected_signal 太模糊」
- **THEN** overall = REJECTED，reject_summary 含 `CHECKLIST_AMBIGUOUS`，orchestrator 退 Stage C，計入 checklist_retry

#### Scenario: Subagent dishonest 連續兩次（deterministic mismatch）
- **WHEN** attempt=0 中 deterministic_results 含 M1 `verdict: FAIL, subagent_self_report_consistent: false`（subagent 自報 M1 completed:true 但 AST 比對發現 stoploss 沒改），attempt=1 仍出現至少一個 deterministic 或 LLM3 `subagent_self_report_consistent: false`
- **THEN** orchestrator TERMINATE；audit log 標記 `SUBAGENT_DISHONEST`、列出涉及的 deterministic + LLM3 item ids

#### Scenario: Subagent dishonest 連續兩次（LLM3 FAIL）
- **WHEN** attempt=0 與 attempt=1 的 llm3_results 均含至少一個 `verdict: FAIL, subagent_self_report_consistent: false`
- **THEN** orchestrator TERMINATE

#### Scenario: Invariant 違反不計入 dishonest
- **WHEN** deterministic_results 中 `timeframe_unchanged` invariant FAIL（無對應 checklist item id）
- **THEN** 該 result `subagent_self_report_consistent: null`；該輪不算 dishonest_attempt；orchestrator 走 IMPLEMENTATION_FAILED 路徑、計入 `subagent_retry`

#### Scenario: Checklist 變更時 dishonest 連續性歸零
- **WHEN** attempt=0 dishonest_attempt 後因 LLM3 INSUFFICIENT 退 Stage C 產生新 checklist，attempt=0' 有 dishonest_attempt
- **THEN** 不視為「連續兩次」（在新 checklist 下這是第 1 次）；orchestrator 不 TERMINATE

---

### Requirement: Stage D/E YAML Artifact 落地與保留

`_run_revise` 在 v2 流程中 SHALL 將三份契約檔實際寫入檔案系統，而非僅存在記憶體物件：

- Stage C 產出的 `checklist.yaml`
- Stage D 產出的 `completion_report.yaml`
- Stage E 每次 audit attempt 產出的 `audit_report.yaml`

**路徑規則**：

- `checklist.yaml` SHALL 寫入 `artifacts/.staging/v{N}/checklist_attempt_{k}.yaml`
- `completion_report.yaml` SHALL 寫入 `artifacts/.staging/v{N}/completion_report_attempt_{k}.yaml`
- `audit_report.yaml` SHALL 寫入 `artifacts/.staging/v{N}/audit_report_attempt_{k}.yaml`
- 其中 `N = analyze_attempt`，`k` 為對應的 checklist/subagent attempt index（0-indexed）

**保留規則**：

- checklist 重產生時，舊的 `checklist_attempt_{k}.yaml` SHALL 保留，不覆寫
- subagent retry / audit retry 時，舊的 `completion_report_attempt_{k}.yaml` 與 `audit_report_attempt_{k}.yaml` SHALL 保留，不覆寫
- revise TERMINATE 時，整個 `artifacts/.staging/v{N}/` SHALL 保留供 forensics
- revise APPROVED 並 promote 後，staging 目錄可保留或清理，但正式 `.py` SHALL 已存在於 `artifacts/strategies/v{N}/`

#### Scenario: Audit attempt 寫出 YAML
- **WHEN** Stage E 第 1 次 audit 完成且 `attempt = 0`
- **THEN** `artifacts/.staging/v{N}/audit_report_attempt_0.yaml` 存在，內容符合本 spec 的 audit_report schema

#### Scenario: Retry 不覆寫舊檔
- **WHEN** subagent 第 2 次重寫、audit 再跑一次
- **THEN** `completion_report_attempt_0.yaml`、`audit_report_attempt_0.yaml` 仍保留，且新增 `completion_report_attempt_1.yaml`、`audit_report_attempt_1.yaml`

---

### Requirement: Unauthorized Change Guard（未授權變更 deterministic 防護）

Stage E deterministic 階段 SHALL 對「checklist 未授權的變更」做獨立檢查，防止 subagent 整份重寫 `.py` 時順手改到 checklist 沒授權的區塊（這是 LLM 整檔重寫的已知風險）。

**授權變更白名單**（從 checklist 衍生）：

對於 checklist 中每個 item，根據 type 推導出受授權變更的 AST 範圍：

- `type: param` 且 `target.kind = class_attr`：類別內 `<name> = ...` 賦值節點
- `type: param` 且 `target.kind = hyperopt_param`：類別內 `<name> = IntParameter(...)`/`DecimalParameter(...)`/`CategoricalParameter(...)` 賦值節點
- `type: param` 且 `target.kind = dict_value`：對應字典字面量中該路徑的 value 節點
- `type: logic` 且 `target.function = <fn>`：類別內方法 `def <fn>(self, ...)` 的 body
- 全域 imports 區塊：永遠視為授權變更（subagent 可能因 logic items 需要新增 indicator import）

**檢查邏輯**：

orchestrator SHALL 在 deterministic 階段比對舊 `.py` 與新 candidate.py 的 AST 差異：

1. 計算所有「結構性變動點」（class attribute 賦值、function body、import statements）
2. 對每個變動點，比對是否落在「授權變更白名單」內
3. 任一變動點落在白名單外 → deterministic_results 加入特殊條目 `{id: "unauthorized_change", verdict: FAIL, evidence: "<變動點描述>", subagent_self_report_consistent: null}`
4. 此特殊條目觸發 IMPLEMENTATION_FAILED 路徑（退 Stage D 重寫，計入 `subagent_retry`）

**例外**：

- 純註解變更、空白/縮排調整（不影響 AST）SHALL NOT 視為未授權變更
- docstring 修改視為未授權變更（避免 LLM 改寫業務語意說明）

#### Scenario: Subagent 順手改了未授權的 method
- **WHEN** checklist 僅授權修改 `populate_indicators`，但新 candidate.py 中 `populate_exit_trend` 的 body AST 也被改動
- **THEN** deterministic_results 加入 `{id: "unauthorized_change", verdict: FAIL, evidence: "populate_exit_trend body modified but not in checklist"}`、overall = REJECTED；orchestrator 退 Stage D 計入 `subagent_retry`

#### Scenario: 授權範圍內的修改不觸發
- **WHEN** checklist 授權修改 `populate_indicators`，subagent 修改 `populate_indicators` 內容
- **THEN** unauthorized_change 檢查 PASS（不出現在 deterministic_results 中，或顯示 verdict: PASS）

#### Scenario: 新增 import 不視為未授權
- **WHEN** subagent 為了實作新 EMA20 計算新增 `import talib.abstract as ta`（或對等）至 imports 區
- **THEN** imports 區塊變動視為授權；不觸發 unauthorized_change

#### Scenario: 純註解變更不觸發
- **WHEN** subagent 在授權範圍外加上一行 `# TODO: refactor` 註解
- **THEN** AST 比對忽略註解差異；不觸發 unauthorized_change

---

### Requirement: LLM3 Audit 輸入隔離

LLM3 audit prompt 的輸入 SHALL 嚴格限定為下列檔案內容，**不得**傳入其他資料：

允許輸入：

- 當輪 checklist.yaml 中 `type: logic` 的子集（每項只保留 `id`、`type`、`target`、`expected_signals`、`forbidden_signals`、`depends_on`）
- 舊 strategy `.py` 完整內容
- 新 strategy `.py` 完整內容（subagent 寫的 candidate）
- 當輪 completion_report.yaml

禁止輸入：

- `revision_intent.md`
- `last_reason`（失敗原因）
- 任何 item 的 `rationale` 欄位
- LLM2 的 intent 審核意見

#### Scenario: rationale 必須剝離
- **WHEN** Stage E 準備 LLM3 prompt
- **THEN** orchestrator SHALL 對每個 logic item 過濾掉 `rationale` 欄位後再注入 prompt

#### Scenario: intent 不得洩漏
- **WHEN** Stage E 構造 LLM3 prompt
- **THEN** prompt 不得含 `revision_intent.md` 內容、`last_reason` 字串、或任何將上述資訊重述的描述
