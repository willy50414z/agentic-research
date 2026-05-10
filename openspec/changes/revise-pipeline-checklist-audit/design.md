## Context

`fix-loop-control-artifacts-and-summary` 引入的 2-LLM revise 流程實際運行後暴露根本性缺陷：LLM 審核的對象（`revised_params.json`）跟最終被 freqtrade 執行的對象（`.py` strategy 檔案）完全脫鉤。一個實測 case 的證據：

- Card `1770413052257109747` 設 `max_loops=2` 但實跑 3 輪
- 三輪 IS/OOS metrics 完全相同（IS pf=2.201 wr=0.341 / OOS pf=1.282 wr=0.284）
- v0 與 v2 的 `TurtleTradingStrategy.py` 為 byte-identical
- LLM 在每輪都產出新的 `revised_params.json`，但 `_real_implement` 只用 `shutil.copy2` 把原始 `.py` 複製到當輪 work_dir
- Freqtrade 用的 `--strategy-path` 指向原始 `.py` 位置（從未被改寫），class 內 `IntParameter(default=...)` 等 default 值決定實際執行參數

**根因分類**：

1. **SoT 錯位**：strategy 參數的真正 source of truth 是 `.py`（class attribute / hyperopt parameter default），但修訂流程只動 dict
2. **審核錯位**：審核者（LLM2 in `revise_validate`）看 dict-vs-dict，看不到 `.py`
3. **執行錯位**：implement 階段沒有 strategy code regeneration 邏輯，只負責呼叫 freqtrade
4. **無自我驗證**：沒有 deterministic check 確認「revise 真的生效了」

並列三個次要問題，雖較小但同樣源自設計疏漏：
- `max_loops` 從 card 同步到 DB 的時序在某些 race 下失效
- summary 檔名 `v{loop_index}_{max_loops}` 用了永遠為 0 的 `loop_index`
- `is_zip` / `oos_zip` 在 `_upload_new_artifacts` 與 `_upload_iteration_zip` 兩條路徑各上傳一次

## Goals / Non-Goals

**Goals:**

- Strategy `.py` 在每輪 revise 後**確實**反映修訂內容；新增 deterministic check 作為 hard guard
- Revise 流程責任邊界清楚：每個 LLM 角色（intent proposer / intent auditor / checklist producer / code subagent / code auditor）有獨立的輸入/輸出契約
- 每輪 iteration 有獨立的 `.py` 檔案與 strategy spec 快照，提供完整 traceability
- 修正三個次要 bug：`max_loops` 同步、summary 檔名、is/oos zip 重複上傳

**Non-Goals:**

- 不引入新 LLM 框架（沿用現有 `_call_llm` subprocess pattern）
- 不引入 hyperopt 動態優化（仍是 default-value-based backtest）
- 不重構整個 workflow dispatcher，只動 `_run_revise` 與相關 prompts/helpers
- 不改變 PASS 路徑（summarize_step）的行為
- 不改 Planka custom field schema、不改 DB schema

## Decisions

### D1：選方案 2（直接改 `.py`）而非方案 1（改 spec 再重生）

**決定**：revise 階段直接寫新 `.py` + 更新 `plan.strategy_file`，implement 不再參與 code generation。

**為什麼不選方案 1**：
- spec.raw_md 是 spec_review 階段確認的契約；每輪改 spec 破壞「審核完成」語意
- 改 spec 後仍需 plan_step 重跑 LLM 生 `.py`，最終仍要解決「`.py` 是否確實對齊 spec」問題，等於 detour
- 現有 freqtrade config 不帶 strategy 參數（`config_generator.py` 只生市場/exchange/fee），改 config 路徑走不通

**方案 2 的代價**：每輪都要 LLM 重寫 `.py`（token 較高）。但這是策略執行正確性的必要成本；deterministic patcher 留待後續優化。

### D2：Intent → Checklist → Subagent → Audit 五階段

**決定**：

```
LLM1 (intent.md, 自然語言)
   ↓
LLM2 (intent audit, retry≤2 with LLM1)
   ↓ approved
LLM2 (intent → checklist.yaml, 結構化、鎖定)
   ↓
Subagent (checklist + 舊 .py → 新 .py + completion_report.yaml)
   ↓
Audit Layer A: Deterministic (param items + invariants)
Audit Layer B: LLM3 (logic items only)
   ↓ pass
done
   ↓ fail
Subagent retry (≤2) with audit reason
```

**為什麼分階段**：
- Intent 是高槓桿決策（決定改什麼方向），需獨立審核
- Checklist 是契約（subagent 對它負責、audit 對它驗證）；鎖定後不可變更
- Subagent 角色純執行，不做策略決策
- Audit 雙層：deterministic 處理機械項（廉價且 100% 準）、LLM3 處理語意項

**為什麼 LLM3 而非沿用 LLM1**：
- LLM1 是 intent 提出者，對自己 intent 衍生的 checklist 會 self-affirmation
- LLM3 prompt 不接受 `intent.md` 與 `rationale`，純粹比對 checklist 與 `.py`，避免被引導蓋章

**替代方案**：單一 LLM 同時 propose + write + self-audit。否決，因為這正是現有設計的失敗模式。

### D3：Checklist 為「鎖定契約」而非可變工作清單

**決定**：LLM2 產出 `checklist.yaml` 後立刻 lock，subagent 與 audit 都對這份原始 checklist 負責，不允許中途修改。

```yaml
locked: true   # subagent / audit 不得修改任一項
```

**為什麼**：
- subagent 若能改 checklist，audit 失去基準
- 若 checklist 本身有問題（例如兩項邏輯互斥），subagent 應 raise「無法執行」並退回 LLM2 修 checklist → 重新 audit；不該自行調整
- 「契約」這個語意一旦鬆動，整個 audit 的可信度就崩潰

### D4：Deterministic Check 必跑且優先

**決定**：

```
Audit 順序：
  1. Deterministic Layer (param items + invariants)
     ↓ 任一 fail → 直接 REJECT，不跑 LLM3
     ↓ all pass
  2. LLM3 Layer (logic items only)
```

**為什麼 deterministic 必跑**：
- 上次 bug 的核心特徵是「LLM 審核蓋章但 `.py` 沒改」；deterministic AST 比對 `IntParameter(default=X)` vs checklist 期望值是廉價、100% 確定的檢查
- 任何純參數類修訂（改 stoploss、改 RSI period、改 minimal_roi）全部走 deterministic，不依賴 LLM 自律
- LLM3 只處理 deterministic 無法判斷的語意項（新增 indicator、新增 logic）

**為什麼 deterministic 優先**：
- Fail-fast 省 token 與時間
- 如果 deterministic 已 fail，邏輯項就算正確也沒意義（`.py` 已壞）

### D5：Subagent 必須自報 completion_report

**決定**：subagent 寫完 `.py` 後產出 `completion_report.yaml`，逐項回報每個 checklist item 是否完成、改在第幾行。

```yaml
items:
  - id: M1
    completed: true
    location: "new.py:42"
  - id: M3
    completed: false
    blocking_reason: "..."
```

**為什麼**：
- LLM 寫程式時偶爾幻覺式說「我做了 X」，forced honest report 逼它先檢查自己
- Audit 用「自報 vs 實際」交叉比對：自報完成但 audit fail → `SUBAGENT_DISHONEST` 訊號（連續兩次直接 TERMINATE 該輪）
- 自報「無法完成」直接 reject，不必跑 audit

### D6：每輪寫獨立 `.py` 到隔離目錄，採 staging path 生命週期

**決定**：

```
寫入路徑分兩階段：

  Stage D 期間（subagent 寫 + retry）:
    artifacts/.staging/v{n}/candidate.py
    artifacts/.staging/v{n}/completion_report.yaml
    artifacts/.staging/v{n}/audit_report_attempt_{k}.yaml   ← k = 0,1,2

  Audit 通過後 promote:
    artifacts/strategies/v{n}/{StrategyName}.py    ← 正式 SoT
    plan.strategy_file = "artifacts/strategies/v{n}/{StrategyName}.py"
    freqtrade --strategy-path artifacts/strategies/v{n}/

  Audit FAIL TERMINATE:
    artifacts/.staging/v{n}/   保留供 forensics
    artifacts/strategies/v{n}/ 不存在（沒 promote）
    plan.strategy_file 維持指向 v{n-1} 的舊 .py
```

**為什麼採 staging**：
- 避免 audit fail 後仍把不合格 `.py` 留在 `artifacts/strategies/v{n}/`，後續若 retry 或 implement 邏輯誤讀就會吃到失敗版本
- Promote 動作是原子的（mv 或 atomic copy）：要嘛全有要嘛全無
- TERMINATE 時 staging 仍保留，整段 audit 歷程可追
- `artifacts/strategies/` 目錄下永遠只有「通過 audit 的 .py」，語意乾淨

**為什麼用獨立目錄而非單檔案**：
- Freqtrade `--strategy-path` 是目錄不是檔案，會掃所有 `.py` 找 class name；如果同目錄殘留舊版本，可能抓錯
- 每輪獨立目錄保證 freqtrade 只看到當輪 `.py`
- 同時自然解決問題 4（每輪 strategy spec 快照）：`.py` 本身就是該輪的權威 spec

**Strategy spec snapshot 的 SoT 紀律**（修正自上版設計）：
- `v{n}_strategy_spec.md` 內容必須**優先從通過 audit 的 `.py`** 透過 deterministic 解析（AST）萃取參數值
- LLM 在產生此快照時只能補充「自然語言摘要」（例如本輪修訂動機、與前輪 delta 的人類可讀描述）；**不可生成參數值**
- 若 deterministic 萃取失敗，該欄位寫 `<unparseable>`，不允許 LLM 推測填入
- 此規則確保 strategy_spec.md 與 `.py` 永不相互背離（避免重蹈本次 bug 的覆轍）

### D7：Retry 邊界硬上限

**決定**：

| Retry 環節 | 上限 | 超過動作 |
|---|---|---|
| LLM1 ↔ LLM2 intent 來回 | 2 次 | TERMINATE 該輪 revise |
| Subagent ↔ Audit code 來回 | 2 次 | TERMINATE 該輪 revise |
| Subagent dishonest（連續） | 2 次 | TERMINATE 該輪 revise |

TERMINATE 該輪 revise 的語意：當輪 `last_result = TERMINATE`，dispatch 走 terminate_summarize_step，把整個 audit log 寫進 `v{n}_audit.md` 上傳 Planka。

**為什麼設上限**：
- LLM1↔LLM2 對不上時可能無限對話
- Subagent 反覆寫錯時應該停下來、保留證據，而非耗光 token

### D8：max_loops 同步時序加 sanity assertion

**決定**：在 `dispatch_bg` 內讀完 card custom field 後立刻 `merge_config` 並重新 `get_project`，log 出 `max_loops` 實際進入 state 的值；如果與 card 值不一致，log warning 但不阻擋（避免 card 改 schema 時整個 pipeline 卡死）。

**為什麼**：
- 上次 bug 不確定是 sync 沒跑、跑了被覆寫、還是 fallback 默默生效
- log 加 assertion 能讓下次出問題時直接看到「sync 確實寫了 2，但 _build_state 讀到 3」這類證據

### D9：Summary 檔名用實際 iteration 範圍

**決定**：

```
舊：v{loop_index}_{max_loops}_summary_report.md   # 永遠 v0_3
新：v{first_iter}_{last_iter}_summary_report.md   # 例如 v0_1（共 2 輪）或 v0_0（只跑 1 輪）
```

`first_iter` = 0（baseline 永遠是第 0 輪）；`last_iter` = `analyze_attempt - 1`。

**為什麼**：
- `loop_index` 在 FAIL 路徑永遠不變（只在 summarize_step 才 +1），用它命名沒意義
- 用 iteration 範圍直接反映實際執行情況，跟報告內 Loop 0/1/2 表格一致

### D11：Subagent 失敗的兩種獨立路徑（修正路徑歧義）

**決定**：subagent 在 `completion_report.yaml` 中明確區分兩種失敗模式，各自走獨立 retry 環節：

```
UNIMPLEMENTABLE_CHECKLIST
  含義: subagent 認為 checklist 本身有問題（item 互斥、目標不存在、
        expected_signal 與舊 .py 結構衝突無法落地）
  證據要求: completion_report.yaml.unimplementable_items[].blocking_reason
  退回路徑: 退回 Stage C，由 LLM2 重產 checklist
  Retry counter: checklist_retry（上限 2 次）
  Retry 觸發條件: 至少一項 item 標 blocking_reason

IMPLEMENTATION_FAILED
  含義: checklist 本身合理，但 subagent 寫的 .py 沒通過 audit
        （deterministic mismatch、LLM3 FAIL、syntax error 等）
  證據要求: audit_report.yaml.overall = REJECTED
  退回路徑: 退回 Stage D，由 subagent 重寫 .py（checklist 不變）
  Retry counter: subagent_retry（上限 2 次）
  Retry 觸發條件: completion_report 全部 completed:true 但 audit FAIL
```

**為什麼分兩種**：
- 兩種失敗的修法不同：UNIMPLEMENTABLE 要改 checklist、IMPLEMENTATION_FAILED 要改 code
- 共用 retry counter 會導致 subagent 連續失敗時錯誤地把 checklist 也算進來、或反過來
- Orchestrator 必須先看 completion_report，再決定走哪條路徑：
  - 若 `unimplementable_items` 非空 → UNIMPLEMENTABLE_CHECKLIST 路徑
  - 若 `unimplementable_items` 為空但 audit REJECTED → IMPLEMENTATION_FAILED 路徑

**Retry counter 互動規則**：
- 兩個 counter 各自獨立、互不重置
- 任一 counter 超 2 次 → TERMINATE 該輪 revise
- 切換路徑（從 Stage D fail 退回 Stage C 又回來）時，subagent_retry 在新 checklist 下歸零；checklist_retry 不歸零

### D12：Feature flag 與 rollout 安全契約

**決定**：新增環境變數 `REVISE_PIPELINE_VERSION`，spec-level 約束：

| 值 | 行為 |
|---|---|
| `v1` | 走舊 2-LLM JSON 流程（`revise.txt` + `revise_validate.txt`） |
| `v2` | 走本 change 新流程（intent → checklist → subagent → audit） |
| 未設或非法值 | 預設 `v1`（保守 rollback 起點）；log warning |

**約束（必須寫入 spec）**：
- 同一 project 在生命週期內，所有 revise 輪次必須使用同一個版本（不可中途切換）
- v1 與 v2 的 artifacts 命名空間獨立（v2 新增的 `intent.md` / `checklist.yaml` / `audit_report.yaml` 在 v1 不存在；v1 的 `revised_params.json` 在 v2 不存在）
- 切換 flag 不影響已完成 project 的歷史 artifacts
- 切換 flag 不需 DB migration

**Rollout 階段**：
1. 部署 v2 程式碼，flag 預設 `v1`，無行為變更
2. 對單一 test project 設 `REVISE_PIPELINE_VERSION=v2` 跑驗收（max_loops=2，驗證 v0/v1 backtest metrics 不同）
3. flag 預設改 `v2`，舊 project 維持 `v1`（因其 plan_step 已用 v1 起跑）
4. 觀察 ≥1 週後移除 v1 程式碼路徑與舊 prompts

### D13：Subagent dishonest 判定涵蓋 deterministic 與 LLM3 雙層

**決定**：`subagent_self_report_consistent` 欄位 SHALL 在 deterministic_results 與 llm3_results 兩層都填寫；連續兩輪 dishonest_attempt 即 TERMINATE。

**為什麼擴到 deterministic 層**：

上次 bug 的精確特徵是：subagent 自報「stoploss 從 -0.05 改成 -0.03 完成」，但 AST 比對 .py 的 class attribute `stoploss` 仍為 -0.05。**這正是 deterministic param check 該抓的反 self-affirmation 場景**。如果 dishonest 只綁 LLM3，subagent 對 param 修改可以反覆亂報，每次都只計入普通 subagent_retry，吃滿 retry 才被擋下——失去這次設計的核心意義。

擴到 deterministic 層後：
- 任一 deterministic param result FAIL + 對應 item 自報 completed:true → `subagent_self_report_consistent: false`
- Invariant 違反不計入（因無 checklist item id 對應；違反 invariant 由其他 retry 機制處理）

### D14：Unauthorized Change Guard

**決定**：deterministic 階段加入「未授權變更」檢查；任何超出 checklist 授權範圍的 AST 結構性變動 → IMPLEMENTATION_FAILED。

**為什麼**：

D2 採整份 `.py` 重寫策略（避免 LLM 寫 patch 出格式錯）。但 LLM 整份重寫有副作用：可能順手改到 checklist 沒授權的區塊（例如改了 `populate_exit_trend` 的條件、改了某個 helper function、改寫 docstring）。

授權白名單從 checklist 衍生：

```
type=param + class_attr        → 授權該 attr 賦值節點
type=param + hyperopt_param    → 授權該 IntParameter/DecimalParameter 節點
type=param + dict_value        → 授權該 dict path 的 value 節點
type=logic + target.function   → 授權該 function 整個 body
imports                        → 永遠授權（為實作 logic items 可能需要新增 import）
註解 / 空白 / 縮排              → 不算結構性變動
docstring                      → 視為未授權（避免 LLM 改寫業務語意說明）
```

任何結構性變動點不在白名單內 → deterministic_results 加 `unauthorized_change` 特殊條目（`subagent_self_report_consistent: null`，因無對應 checklist item id）；觸發 IMPLEMENTATION_FAILED 路徑。

**為什麼不選擇「直接寫 patch 而非整檔重寫」**：

D2 已說明寫 patch 對 LLM 是反模式（容易格式錯）。這次選擇是「保留整檔重寫 + 用 deterministic guard 限制副作用」。Guard 失敗時讓 subagent 重試一次，通常 LLM 第二次能避開。

### D10：is/oos zip 去重

**決定**：`_upload_new_artifacts` 過濾 `type ∈ {is_zip, oos_zip}`，僅留給 `_upload_iteration_zip` 透過 `v{n}_backtest.zip` 統一上傳。

**為什麼**：
- 卡片同時出現 `v0_is.zip`、`v0_oos.zip`、`v0_backtest.zip` 三個附件，後者已包含前兩者
- 影響 reviewer 視覺體驗、但不影響功能；屬低風險清理

## Risks / Trade-offs

| Risk | Mitigation |
|---|---|
| LLM 寫 `.py` 偶爾出 syntax error | Subagent prompt 要求自我語法檢查；deterministic check Layer 0 加 `ast.parse()` 驗 syntax；fail 即 retry |
| LLM3 過鬆（傾向 PASS 避免 retry） | Audit prompt 明確說「INSUFFICIENT 才是正確行為」；不給 intent.md / rationale 避免被引導；deterministic Layer A 處理大宗 |
| Checklist 過於模糊（expected_signal 寫法不準） | Prompt 中提供「好/壞 expected_signal 範例」；LLM2 產 checklist 時要求每條 signal 都對應到具體 function/code area |
| Subagent 連續 dishonest | Forced retry，重複兩次直接 TERMINATE，audit log 上傳 Planka 供人類介入 |
| Token 成本上升（多了 LLM3 + retry 回路） | 大宗修訂走 deterministic（0 token）；只有 logic items 進 LLM3；retry 上限避免失控 |
| 每輪獨立 `.py` 目錄占空間 | 純 markdown / py 檔，數百 KB 量級可忽略 |
| `--strategy-path` 隔離目錄與 freqtrade userdir 慣例的微小不對齊 | 文件化；對 freqtrade CLI 行為無影響（已有專案先例使用 `--strategy-path` 指向 work_dir） |
| Migration：在跑中的 project state 包含舊格式 artifacts | revise_step 對舊欄位做 backward-compatible 解析（讀舊 plan.strategy_file 仍可用，新流程從本次 revise 開始生效） |

## Migration Plan

**部署順序**：

1. 部署新 prompts、新 modules（`audit.py`、`checklist.py`），但 `revise_step` 仍走舊路徑（feature flag `REVISE_PIPELINE_VERSION` 或環境變數）
2. 在測試 project 跑完整 revise 迴圈，驗證每階段 artifact 正確產出、deterministic check 對應 expected
3. 切換 `revise_step` 到新流程；舊 prompts (`revise.txt`、`revise_validate.txt`) 保留一個版本作為 rollback 起點
4. 觀察 1~2 個 production project 完整跑完的 audit log；確認沒有 `SUBAGENT_DISHONEST` 高頻發生
5. 移除舊 prompts 與 fallback 邏輯

**Rollback**：透過 env var 切回舊 `revise_step`（保留至少一個 release）。新 artifacts 不會干擾舊流程（命名空間獨立）。

## Open Questions

- LLM3 audit prompt 對「forbidden_signals」的列表，是固定一份（look-ahead bias、未 shift 的未來 bar 引用）還是 LLM2 在每次 checklist 中動態產出？傾向固定一份 + LLM2 可追加 case-specific 項。
- Checklist `type: param` 中 `target.kind = dict_value` 的 path 表示法（例如 `minimal_roi."0"`）是否需要支援陣列索引？目前 freqtrade 策略中無此 case，先不支援。
- Subagent 寫整份 `.py` 還是 patch？目前傾向整份重寫（更穩），但需要 deterministic Layer A 加「未變動區塊保留度」檢查（diff 行數不能爆量），避免 LLM 順手改不該改的地方——這項實作細節留到 tasks 階段定。
- 是否需要把 `intent.md` / `checklist.yaml` / `audit_report.yaml` 也上傳 Planka（除了 `v{n}_audit.md` 摘要）？傾向只上傳人類可讀的 markdown 摘要，YAML 留在 work_dir 供 debug。
