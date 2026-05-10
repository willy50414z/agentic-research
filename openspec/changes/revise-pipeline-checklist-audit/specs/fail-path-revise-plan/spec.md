## MODIFIED Requirements

### Requirement: REVISE step 執行多階段修訂流程

`_run_revise` handler 在 `REVISE_PIPELINE_VERSION=v2` 下 SHALL 依序執行下列五個階段，取代原本的 2-LLM JSON 驗證流程：

1. **Stage A — Intent 提案（LLM1）**：讀取當前 `implementation_plan`、`last_reason`（失敗原因）、舊 `.py` 內容 → 產出 `revision_intent.md`（自然語言描述本輪要改什麼方向、為什麼）
2. **Stage B — Intent 審核（LLM2）**：讀取失敗原因 + intent → 判定 APPROVED / REJECTED；REJECTED 時系統 SHALL 將意見回灌 LLM1 重提 intent，`intent_retry` 計數上限為 2 次
3. **Stage C — Checklist 產出（LLM2）**：intent APPROVED 後，LLM2 SHALL 將 intent 翻譯為 `checklist.yaml`（鎖定不可變更的修改契約，schema 見 `revise-checklist-protocol`）；`checklist_retry` 計數上限為 2 次
4. **Stage D — Subagent 改寫 `.py`（staging path）**：subagent SHALL 讀取 checklist + 舊 `.py` 寫出新 `.py` 至 staging 路徑 `artifacts/.staging/v{N}/candidate.py`，並產出 `completion_report.yaml`；`subagent_retry` 計數上限為 2 次
5. **Stage E — Audit 雙層驗證**：
   - **Layer A（Deterministic）**：對 checklist 中 `type: param` 項目與全域 invariants（timeframe、class name、order_types 四鍵齊全、no look-ahead pattern）做 AST/regex 比對
   - **Layer B（LLM3）**：對 `type: logic` 項目做 expected_signals / forbidden_signals 比對；輸入隔離規則見 `revise-checklist-protocol`
   - audit_report.yaml 的 verdict 計算與失敗路徑路由（見下方分流規則）

**失敗路徑分流（修正先前路徑歧義）**：

orchestrator 在 Stage D/E 之間 SHALL 依下列順序判定失敗模式：

| 觸發條件 | 失敗模式 | 退回階段 | 計入 counter |
|---|---|---|---|
| `completion_report.unimplementable_items` 非空 | `UNIMPLEMENTABLE_CHECKLIST` | Stage C（重產 checklist） | `checklist_retry` |
| `completion_report.unimplementable_items` 空 + 任一 item `completed: false` | `IMPLEMENTATION_FAILED` | Stage D（subagent 重寫） | `subagent_retry` |
| audit `overall: REJECTED` 且 `reject_summary` 含 `CHECKLIST_AMBIGUOUS`（LLM3 INSUFFICIENT） | `UNIMPLEMENTABLE_CHECKLIST`（checklist 表述問題） | Stage C | `checklist_retry` |
| audit `overall: REJECTED` 其他原因（deterministic FAIL / LLM3 FAIL） | `IMPLEMENTATION_FAILED` | Stage D | `subagent_retry` |
| 連續兩輪 audit 出現任一 deterministic 或 LLM3 result `subagent_self_report_consistent: false`（詳細判定見 `revise-checklist-protocol`） | `SUBAGENT_DISHONEST` | TERMINATE 該輪 revise | — |

**Retry counter 互動規則**：

- `intent_retry`、`checklist_retry`、`subagent_retry` 三者各自獨立、互不重置
- 任一 counter 超 2 次（即第 3 次仍失敗）→ TERMINATE 該輪 revise
- 從 Stage D fail 退回 Stage C 並重產 checklist 後再回 Stage D，`subagent_retry` 在新 checklist 下歸零；`checklist_retry` 不歸零
- 切換 retry 路徑不影響 `intent_retry`（intent 已通過後不再重審）

**Promote 規則**：

- 僅當 audit `overall: APPROVED` → orchestrator SHALL 將 `artifacts/.staging/v{N}/candidate.py` promote（atomic move 或 copy）至 `artifacts/strategies/v{N}/{StrategyName}.py`，並更新 `plan.strategy_file`
- audit 從未 APPROVED 而 TERMINATE → staging 目錄保留供 forensics，`artifacts/strategies/v{N}/` 不得建立，`plan.strategy_file` 維持指向上一輪（v{N-1}）的路徑

**LLM 不可用**：

- 任一階段 LLM call 失敗（subprocess error / timeout / 解析失敗） → 系統 SHALL 設 `last_result = TERMINATE`，記錄失敗階段，進入 terminate_summarize
- v2 流程不提供 rule-based fallback；舊 `revise.txt` 與 `revise_validate.txt` prompt 仍保留供 v1 使用

#### Scenario: 完整流程一次通過 + promote
- **WHEN** Stage A 產出 intent、Stage B 第一次 APPROVED、Stage C checklist 產出、Stage D subagent 寫至 staging、Stage E audit 全 PASS
- **THEN** orchestrator 將 staging candidate.py promote 至 `artifacts/strategies/v{N}/{StrategyName}.py`、`implementation_plan.strategy_file` 對應更新、`workflow_step` 設為 `implement`

#### Scenario: Intent 審核 REJECTED 達上限
- **WHEN** Stage B 連續三次（intent_retry=0,1,2）回 REJECTED
- **THEN** 系統 TERMINATE 該輪、staging 不建立、`plan.strategy_file` 維持上一輪路徑

#### Scenario: UNIMPLEMENTABLE_CHECKLIST 路徑
- **WHEN** subagent 第一次回 `unimplementable_items: [M3]`
- **THEN** orchestrator 退回 Stage C 由 LLM2 重產 checklist；`checklist_retry` +1；新 checklist 產生後 `subagent_retry` 歸零

#### Scenario: IMPLEMENTATION_FAILED 路徑
- **WHEN** subagent 自報全 completed:true、`unimplementable_items: []`，但 audit deterministic FAIL（M1 stoploss 值不符）
- **THEN** orchestrator 退回 Stage D 帶 mismatch 細節；`subagent_retry` +1；checklist 不變、`checklist_retry` 不變

#### Scenario: CHECKLIST_AMBIGUOUS 退回 Stage C
- **WHEN** audit deterministic 全 PASS、LLM3 對 M3 回 INSUFFICIENT、reject_summary 含 `CHECKLIST_AMBIGUOUS`
- **THEN** orchestrator 退回 Stage C 由 LLM2 重寫該項 expected_signals；`checklist_retry` +1；不計入 `subagent_retry`

#### Scenario: Subagent 連續 dishonest（含 deterministic mismatch）
- **WHEN** attempt=0 deterministic_results 含 M1 `verdict: FAIL` 但 completion_report 自報 M1 completed:true（典型上次 bug 模式：自報改了 stoploss 但 AST 看 .py 沒改），attempt=1 仍出現任一層級的 `subagent_self_report_consistent: false`
- **THEN** 系統 TERMINATE，無視其他 retry counter；audit log 標記 root cause = `SUBAGENT_DISHONEST`、列出涉及的 deterministic 與 LLM3 item ids

#### Scenario: Audit fail 後 staging 不可被誤讀
- **WHEN** 整輪 revise TERMINATE，staging 仍含 candidate.py
- **THEN** `_real_implement` 與後續 backtest SHALL NOT 從 staging 讀取；freqtrade `--strategy-path` 指向 `artifacts/strategies/v{N-1}/`（上一輪通過 audit 的目錄）

#### Scenario: LLM 不可用
- **WHEN** 任一 LLM call 失敗（subprocess error / timeout）
- **THEN** 系統 SHALL 設 `last_result = TERMINATE`、記錄失敗階段、staging 不 promote、進入 terminate_summarize

---

### Requirement: REVISE step 產出修訂方向文件並上傳 Planka

`_run_revise` handler 在 Stage A 完成後 SHALL 產出 `v{N}_revised_direction.md`（N = `analyze_attempt`），內容為 `revision_intent.md` 經 Stage B 審核通過的最終版本（自然語言描述修訂方向）。

`_run_revise` handler 在 Stage E 完成後 SHALL 額外產出 `v{N}_audit.md`，內容包含 deterministic check 結果、LLM3 audit 結果、retry 歷史、以及最終 verdict。

兩份文件 SHALL 以 `upload_spec_attachment` 上傳至 Planka 卡片。上傳失敗 SHALL 僅 log warning，不中斷 workflow。

#### Scenario: 修訂方向與 audit 文件均上傳成功
- **WHEN** Stage A intent APPROVED、Stage E audit 通過
- **THEN** Planka 附件出現 `v{N}_revised_direction.md` 與 `v{N}_audit.md`

#### Scenario: TERMINATE 路徑也上傳 audit 文件
- **WHEN** revise 任一階段 TERMINATE
- **THEN** `v{N}_audit.md` 仍須上傳，內容反映 TERMINATE 原因與 retry 歷史；`v{N}_revised_direction.md` 視 Stage A 是否已產出而定

#### Scenario: 上傳失敗不中斷流程
- **WHEN** `upload_spec_attachment` 拋出例外
- **THEN** 系統 SHALL log warning，本地檔案保留，workflow 繼續

## ADDED Requirements

### Requirement: Checklist 為鎖定契約

`_run_revise` 在 Stage C 產出的 `checklist.yaml` 一旦 commit（`locked: true`），subagent 與 audit 階段 SHALL 視其為不可變更的契約：

- Subagent SHALL NOT 自行新增、刪除或修改任一 checklist item；任何修改建議 SHALL 被 orchestrator 忽略
- Audit SHALL 對原始 checklist 比對，不接受運行時動態修改
- 若 subagent 偵測某項 checklist 不可實作（item 互斥、目標 function 不存在、expected_signal 與舊 .py 結構衝突等），SHALL 在 `completion_report.yaml.unimplementable_items` 列入該 item id，並於對應 item 填 `blocking_reason`；orchestrator 走 `UNIMPLEMENTABLE_CHECKLIST` 分流（退 Stage C，計入 `checklist_retry`）

`completion_report.yaml` 與 `checklist.yaml` 的 schema 細節見 `revise-checklist-protocol`。

#### Scenario: Subagent 嘗試修改 checklist 被拒
- **WHEN** subagent 在輸出中提出修改某 checklist item 的建議文字
- **THEN** orchestrator SHALL 忽略該建議；若 audit fail 仍計入 `subagent_retry`

#### Scenario: Subagent 回報 unimplementable
- **WHEN** subagent `completion_report.yaml.unimplementable_items` 含 `[M3]`
- **THEN** orchestrator 退回 Stage C 由 LLM2 重產 checklist；計入 `checklist_retry`，新 checklist 產生後 `subagent_retry` 歸零

---

### Requirement: 修訂流程 retry 上限與獨立 counter

`_run_revise` SHALL 維護下列三個獨立的 retry counter，互不重置（除 `subagent_retry` 在新 checklist 產生時歸零外）：

| Counter | 觸發 +1 條件 | 上限 | 超過動作 |
|---|---|---|---|
| `intent_retry` | Stage B 對 intent 回 REJECTED | 2 | TERMINATE 該輪 revise |
| `checklist_retry` | UNIMPLEMENTABLE_CHECKLIST 或 CHECKLIST_AMBIGUOUS 觸發退 Stage C | 2 | TERMINATE 該輪 revise |
| `subagent_retry` | IMPLEMENTATION_FAILED 觸發退 Stage D | 2 | TERMINATE 該輪 revise |

額外 TERMINATE 觸發條件：

- `SUBAGENT_DISHONEST` 連續兩輪：無論其他 counter 狀態，立即 TERMINATE，標記 root cause

任一 counter 超 2 次（即第 3 次仍失敗）或 dishonest 連續達 2 → 系統 SHALL 將完整 audit log 寫入 `v{N}_audit.md`、設 `last_result = TERMINATE`，dispatch 進入 terminate_summarize。

#### Scenario: intent_retry 達上限
- **WHEN** LLM1 提出 intent → REJECTED → 重提 → REJECTED → 第 3 次仍 REJECTED
- **THEN** 系統 TERMINATE，root cause 標記 `INTENT_RETRY_EXHAUSTED`

#### Scenario: checklist_retry 達上限
- **WHEN** UNIMPLEMENTABLE_CHECKLIST 連續三次觸發
- **THEN** 系統 TERMINATE，root cause 標記 `CHECKLIST_RETRY_EXHAUSTED`

#### Scenario: subagent_retry 達上限
- **WHEN** 同一份 checklist 下，subagent 寫 .py → audit FAIL → 重寫 → audit FAIL → 第 3 次仍 FAIL
- **THEN** 系統 TERMINATE，root cause 標記 `SUBAGENT_RETRY_EXHAUSTED`

#### Scenario: subagent_retry 在新 checklist 下歸零
- **WHEN** `subagent_retry = 1` 後因 LLM3 INSUFFICIENT 退回 Stage C 產生新 checklist
- **THEN** `subagent_retry` 重設為 0；`checklist_retry` +1 不變

#### Scenario: Dishonest 連續達上限
- **WHEN** attempt=0 與 attempt=1 的 audit_report 中（合計 deterministic_results 與 llm3_results）均含至少一個 `subagent_self_report_consistent: false`
- **THEN** 系統立即 TERMINATE，root cause 標記 `SUBAGENT_DISHONEST`，無視其他 counter

---

### Requirement: REVISE_PIPELINE_VERSION feature flag

`_run_revise` 行為 SHALL 由環境變數 `REVISE_PIPELINE_VERSION` 控制：

| 值 | 行為 |
|---|---|
| `v1` | 走舊 2-LLM JSON 驗證流程（沿用 `revise.txt` + `revise_validate.txt`，無 staging path） |
| `v2` | 走本 spec 定義的多階段流程（intent → checklist → subagent → audit + staging path） |
| 未設或非 v1/v2 | 預設 `v1`；同時 log warning |

不變式約束：

- 同一 project 在生命週期內，所有 revise 輪次 SHALL 使用同一個版本；orchestrator 在 dispatch 時讀取一次 flag、寫入 `projects.config.revise_pipeline_version`，後續輪次以該欄位為準（避免中途切換造成 artifacts 命名空間混亂）
- v1 與 v2 的 artifacts 命名空間獨立：v2 才有 `intent.md` / `checklist.yaml` / `audit_report.yaml` / `.staging/` 目錄；v1 才有 `revise_draft.json` / `revised_params.json`（純 dict 修訂）
- 切換 flag SHALL NOT 觸發 DB schema 變更；SHALL NOT 影響已完成 project 的歷史 artifacts
- Rollback 行為：將 flag 改回 `v1` 即恢復舊流程；新建立的 project 沿用新 flag，已執行中的 project 仍按其 `projects.config.revise_pipeline_version` 跑完

#### Scenario: 預設 v1 不變更行為
- **WHEN** 環境未設 `REVISE_PIPELINE_VERSION`
- **THEN** `_run_revise` 走 v1 流程（舊 2-LLM JSON），與本 change 部署前行為一致

#### Scenario: 顯式設定 v2
- **WHEN** `REVISE_PIPELINE_VERSION=v2`、project 首次進入 revise
- **THEN** orchestrator 走 v2 流程；`projects.config.revise_pipeline_version = "v2"` 寫入 DB

#### Scenario: 同一 project 中途切 flag 不影響行為
- **WHEN** project 已記錄 `revise_pipeline_version=v2`，部署將環境 flag 改為 v1
- **THEN** 該 project 後續輪次仍走 v2（以 DB 記錄為準）；新建立的 project 走 v1

#### Scenario: 非法值 fallback
- **WHEN** `REVISE_PIPELINE_VERSION=v3`（未支援值）
- **THEN** 系統 SHALL log warning「unknown REVISE_PIPELINE_VERSION 'v3'; fallback to v1」、走 v1 流程
