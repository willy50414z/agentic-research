## 1. Checklist / CompletionReport / AuditReport Schema 與 Audit 模組

- [x] 1.1 在 `app/freqtrade/checklist.py` 定義 dataclass：`ChecklistItem`（含 type/target/from/to/expected_signals/forbidden_signals/rationale）、`ParamTarget`（kind/name/field/path）、`LogicTarget`（function）、`Checklist`（含 `locked: bool`、`invariants: list[str]`、`items: list`）
- [x] 1.2 定義 `CompletionReport` dataclass：iteration、attempt、items、`unimplementable_items: list[str]`；含 `is_unimplementable() -> bool` 與 `failed_items() -> list[str]` helper
- [x] 1.3 定義 `AuditReport` dataclass：iteration、attempt、deterministic_results、llm3_results、`overall: APPROVED|REJECTED`、reject_summary；其中 result 的 `subagent_self_report_consistent` 型別為 `bool | None`；含 `should_route_to_stage_c() -> bool` helper（用於 INSUFFICIENT/CHECKLIST_AMBIGUOUS 判定）
- [x] 1.4 實作 `parse_checklist(yaml_str)` / `parse_completion_report(yaml_str)` / `parse_audit_report(yaml_str)`，全部須對缺欄位 raise `ValidationError`（對應 `revise-checklist-protocol` spec）
- [x] 1.5 實作 `cross_check(checklist, completion_report) -> RoutingDecision`：回傳 `UNIMPLEMENTABLE_CHECKLIST | IMPLEMENTATION_FAILED | READY_FOR_AUDIT` 三種路由訊號
- [x] 1.6 在 `app/freqtrade/audit.py` 實作 `deterministic_check_param(item, new_py_path) -> CheckResult`：用 `ast` 模組解析新 `.py`，支援 `class_attr`、`hyperopt_param`、`dict_value` 三種 target.kind
- [x] 1.7 在 `app/freqtrade/audit.py` 實作 `deterministic_check_invariants(invariants, old_py, new_py) -> list[CheckResult]`：timeframe_unchanged、class_name_unchanged、order_types_four_keys、no_lookahead_pattern
- [x] 1.8 在 `app/freqtrade/audit.py` 實作 `llm3_audit_logic(items, old_py, new_py, completion_report) -> AuditResult`：呼叫 `_call_llm` 跑 audit prompt；強制過濾掉 item 的 `rationale` 欄位（符合 `revise-checklist-protocol` 輸入隔離規則）
- [x] 1.9 在 `app/freqtrade/audit.py` 實作頂層 `run_audit(checklist, new_py, old_py, completion_report) -> AuditReport`：先跑 deterministic、fail 即 short-circuit；otherwise 跑 LLM3；按 INSUFFICIENT 規則計算 `reject_summary` 是否含 `CHECKLIST_AMBIGUOUS`
- [x] 1.9a 在 `app/freqtrade/audit.py` 或對應 helper 實作 `write_audit_report(report, output_path)`，將每次 Stage E audit attempt 實際寫成 `artifacts/.staging/v{N}/audit_report_attempt_{k}.yaml`
- [x] 1.10 在 `tests/freqtrade/test_audit.py` 覆蓋 deterministic check 各 target.kind 的 PASS/FAIL 案例
- [x] 1.11 在 `tests/freqtrade/test_audit.py` 覆蓋 invariants 檢查（timeframe 變更、class name 變更、order_types 缺鍵、look-ahead pattern 偵測）
- [x] 1.12 在 `tests/freqtrade/test_audit.py` 覆蓋 LLM3 INSUFFICIENT → CHECKLIST_AMBIGUOUS 路由
- [x] 1.13 在 `tests/freqtrade/test_audit.py` 驗證 LLM3 prompt 不含 rationale / intent / last_reason（透過 mock `_call_llm` 抓 prompt 字串斷言）
- [x] 1.14 在 `app/freqtrade/audit.py` 實作 `compute_subagent_self_report_consistent(deterministic_results, llm3_results, completion_report) -> updated_results`：對 deterministic_results 與 llm3_results 中每一項，依 `revise-checklist-protocol` 規則填 `subagent_self_report_consistent`；invariant 結果填 `null`
- [x] 1.15 加 test：deterministic param FAIL + 自報 completed:true → `consistent: false`；deterministic param PASS + 自報 completed:true → `consistent: true`；invariant FAIL → `consistent: null`
- [x] 1.16 加 test：dishonest_attempt 連續性判定，含 checklist 變更時歸零場景
- [x] 1.17 在 `app/freqtrade/audit.py` 實作 `check_unauthorized_changes(old_py, new_py, checklist) -> CheckResult | None`：解析兩份 .py AST、推導授權白名單、比對結構性變動點；回傳 `unauthorized_change` 特殊 result 或 None
- [x] 1.18 授權白名單推導器 helper：`derive_authorized_targets(checklist) -> AuthorizedTargets`，覆蓋 class_attr / hyperopt_param / dict_value / function / imports
- [x] 1.19 加 test：subagent 改了未授權的 method body → 觸發 unauthorized_change FAIL
- [x] 1.20 加 test：純註解 / 空白 / 縮排變動不觸發 unauthorized_change
- [x] 1.21 加 test：新增 import 為了 logic item 實作 → 不觸發 unauthorized_change
- [x] 1.22 加 test：docstring 變更 → 觸發 unauthorized_change FAIL
- [x] 1.23 將 `check_unauthorized_changes` 整合進 `run_audit`：在 deterministic param/invariant 之後執行、結果合併至 `deterministic_results`

## 2. 新 Prompt 檔案

- [x] 2.1 撰寫 `app/prompts/freqtrade/revise_intent.txt`（LLM1 提案 intent）：輸入失敗原因 + 舊 plan + 舊 .py，輸出 `revision_intent.md`
- [x] 2.2 撰寫 `app/prompts/freqtrade/revise_intent_audit.txt`（LLM2 審 intent）：輸入失敗原因 + intent，輸出 APPROVED/REJECTED + 意見
- [x] 2.3 撰寫 `app/prompts/freqtrade/revise_checklist.txt`（LLM2 翻譯 intent → checklist）：含 schema 範例、好/壞 expected_signal 範例
- [x] 2.4 撰寫 `app/prompts/freqtrade/revise_subagent.txt`（subagent 寫 .py + completion_report）：強調自報誠實、checklist 不可變更
- [x] 2.5 撰寫 `app/prompts/freqtrade/revise_audit.txt`（LLM3 audit logic items）：禁止給 intent.md / rationale；強調 INSUFFICIENT 是合法輸出
- [x] 2.6 **保留** `app/prompts/freqtrade/revise.txt` 與 `app/prompts/freqtrade/revise_validate.txt`（v1 路徑仍需要它們），僅在 task 10.8 完成 v2 production 驗證後才移除

## 3. revise_step 多階段 orchestrator 與三段獨立 retry counter

- [ ] 3.1 在 `app/freqtrade/steps.py` 重寫 `revise_step`：改為 orchestrator，外層 dispatch 三個 retry counter (`intent_retry`、`checklist_retry`、`subagent_retry`) 與 dishonest counter
- [ ] 3.2 實作 `_run_intent_stage(state) -> intent_path`：含 LLM1 ↔ LLM2 retry，使用 `intent_retry` counter；超過 2 次 raise `ReviseTerminate(reason="INTENT_RETRY_EXHAUSTED")`
- [ ] 3.2a `_run_intent_stage` 於 Stage B APPROVED 後產出 `v{N}_revised_direction.md`（內容為核准版 intent 最終文案），並將其加入 artifacts 供 `_run_revise` 上傳 Planka
- [ ] 3.3 實作 `_run_checklist_stage(intent_path) -> Checklist`：呼叫 LLM2 翻譯、parse + validate schema、強制 `locked=True`；schema 驗證失敗計入 `checklist_retry`，超過 raise `ReviseTerminate(reason="CHECKLIST_RETRY_EXHAUSTED")`
- [ ] 3.3a `_run_checklist_stage` 每次成功產生 checklist 時，將其寫入 `artifacts/.staging/v{N}/checklist_attempt_{k}.yaml`；重產 checklist 時保留舊檔不覆寫
- [ ] 3.4 實作 `_run_subagent_stage(checklist, old_py, staging_dir) -> tuple[candidate_path, completion_report]`：呼叫 subagent prompt，寫到 `artifacts/.staging/v{N}/candidate.py`
- [ ] 3.4a `_run_subagent_stage` 將 subagent 回傳的 completion report 寫入 `artifacts/.staging/v{N}/completion_report_attempt_{k}.yaml`；retry 時保留舊檔不覆寫
- [ ] 3.5 實作 `_run_audit_stage(checklist, candidate_py, old_py, completion_report) -> AuditReport`：呼叫 `audit.run_audit`
- [ ] 3.6 實作分流邏輯：依 `cross_check` 結果與 audit overall/reject_summary 決定路由（UNIMPLEMENTABLE_CHECKLIST → Stage C；IMPLEMENTATION_FAILED → Stage D；CHECKLIST_AMBIGUOUS → Stage C 但帶 LLM3 INSUFFICIENT 訊息）
- [ ] 3.7 實作獨立 counter 邏輯：UNIMPLEMENTABLE_CHECKLIST 與 CHECKLIST_AMBIGUOUS 兩條路徑都計入 `checklist_retry`；IMPLEMENTATION_FAILED 計入 `subagent_retry`；新 checklist 產生時 `subagent_retry` 歸零
- [ ] 3.8 實作 dishonest counter：每次 audit 計算 deterministic_results + llm3_results **合計**`subagent_self_report_consistent: false` 的 item 數量（涵蓋雙層）；該輪有至少一個 false 即 mark 為 `dishonest_attempt`；連續兩輪 `dishonest_attempt` 直接 raise `ReviseTerminate(reason="SUBAGENT_DISHONEST")`；checklist 變更時歸零連續性
- [ ] 3.9 實作 promote 動作：audit `overall: APPROVED` 時 atomic move/copy `artifacts/.staging/v{N}/candidate.py` → `artifacts/strategies/v{N}/{StrategyName}.py`；partial 失敗視為 promote 失敗 TERMINATE
- [ ] 3.10 實作 TERMINATE 路徑：任一 counter 超 2 次或 LLM 不可用 → 寫 `v{N}_audit.md`（含三 counter 歷史）、保留 staging 不清理、回 `{"last_result": "TERMINATE", "last_reason": ...}`、`plan.strategy_file` 維持指向 v{N-1}
- [ ] 3.11 移除 `revise_step` 中的 rule-based fallback（包含現有 stoploss tighten 邏輯）；rule-based fallback 僅在 v1 流程保留

## 4. 每輪獨立 strategy `.py`、staging path、spec 快照

- [ ] 4.1 修改 `plan_step`：把 `.py` 直接寫入 `artifacts/strategies/v0/{StrategyName}.py`（baseline 無 staging），plan.strategy_file 對應更新
- [ ] 4.2 確認 revise_step subagent 階段寫到 staging（`artifacts/.staging/v{N}/candidate.py`），audit 通過後 promote（task 3.9 涵蓋）
- [ ] 4.3 在 `app/freqtrade/strategy_extractor.py` 新增 deterministic AST 萃取模組：`extract_class_name(py)`、`extract_timeframe(py)`、`extract_stoploss(py)`、`extract_minimal_roi(py)`、`extract_hyperopt_params(py) -> dict[name, default_value]`、`extract_entry_conditions(py)`、`extract_exit_conditions(py)`；對解析失敗的欄位回傳 sentinel `<unparseable>` 字串並 log warning
- [ ] 4.4 在 `app/freqtrade/steps.py` 新增 `_write_strategy_spec_snapshot(py_path, prev_py_path, checklist, intent_md, output_path)`：先用 strategy_extractor 萃取結構性資料、再讓 LLM 補充「修訂摘要」與「delta 描述」兩段自然語言；最終 markdown 結構性區段與 LLM 補充區段以章節標題明確區隔
- [ ] 4.5 在 strategy_extractor 加 unit test：覆蓋標準 freqtrade 策略結構、各種 IntParameter/DecimalParameter/CategoricalParameter 變體、entry/exit 條件含 `&`/`|` 組合、解析失敗 fallback
- [ ] 4.6 plan_step 完成後呼叫 `_write_strategy_spec_snapshot`（prev_py_path=None、checklist=None、intent_md=None）產 `v0_strategy_spec.md`，僅含結構性區段（無 LLM 補充）
- [ ] 4.7 `_run_revise` 在 promote 完成後呼叫 `_write_strategy_spec_snapshot`（prev_py_path=v{N-1} 路徑、checklist=當輪 checklist、intent_md=當輪 intent）產 `v{N}_strategy_spec.md`
- [ ] 4.8 在 `_run_revise` 與 `_run_plan` 中將 `v{N}_strategy_spec.md` 上傳 Planka（透過 sink.upload_spec_attachment）；上傳失敗 log warning 不中斷
- [ ] 4.9 修改 `app/freqtrade/backtest.py:run_backtest_is_oos`：`strategy_dir` 確保指向 `artifacts/strategies/v{N}/`（promote 後路徑），絕不指向 `artifacts/.staging/`
- [ ] 4.10 加 test：`v{N}_strategy_spec.md` 中所有結構性參數值與 `.py` AST 萃取結果一致；LLM 補充區段不得包含參數值（由 prompt 設計 + 後驗 regex 雙重保險）
- [ ] 4.11 加 test：當 `.py` 含無法 AST 解析的欄位，snapshot 對應位置寫 `<unparseable>` 而非 LLM 推測值
- [ ] 4.12 在 `_write_strategy_spec_snapshot` 中實作 delta 區段分流邏輯：對每個 checklist item 依 type 走不同來源（param 用 from→to + AST 比對驗證、logic 用 expected_signals + forbidden_signals + rationale 文字化）；加 source 標註
- [ ] 4.13 加 test：param item delta 顯示 `from → to` 且與 AST 萃取值一致；不一致時 raise error
- [ ] 4.14 加 test：logic item delta 顯示 function + expected_signals + forbidden_signals + rationale 三段；無 from/to 欄位

## 5. Bug 修補：max_loops 同步

- [x] 5.1 修改 `app/api/server.py:run_dispatch_bg`：在 `read_card_custom_fields` → `merge_config` 後重新 `get_project` 確認寫入；log card raw / merged / state 三個值
- [x] 5.2 在 `_build_state` 開頭 log 實際讀到的 `cfg.get("max_loops")` 與最終 state 值
- [x] 5.3 加 sanity check：若 card raw、merged、state 三者不一致 log warning（不阻擋）
- [x] 5.4 在 `tests/api/test_dispatch_bg.py` 加測試覆蓋同步時序與 mismatch warning

## 6. Bug 修補：summary 檔名

- [x] 6.1 修改 `app/workflow/executing_step.py:_run_terminate_summarize`：filename 計算改用 `f"v0_{analyze_attempt - 1}_summary_report.md"`
- [x] 6.2 同步修改 post_comment 中的檔名引用
- [x] 6.3 加 unit test 覆蓋 analyze_attempt = 1, 2, 3 對應檔名

## 7. Bug 修補：is/oos zip 重複上傳

- [x] 7.1 修改 `app/workflow/executing_step.py:_upload_new_artifacts`：過濾 `type ∈ {is_zip, oos_zip, is_result, oos_result, trades, signals, report}`，僅保留 `revised_direction`、`audit`、`strategy_spec` 等 markdown 類別
- [x] 7.2 確認 `_upload_iteration_zip` 仍把 is_zip / oos_zip 等納入 `v{N}_backtest.zip`
- [x] 7.3 加 unit test 覆蓋 `_upload_new_artifacts` 過濾邏輯

## 8. 整合測試與驗證

- [ ] 8.1 在 `tests/freqtrade/test_revise_pipeline.py` 撰寫端到端 mock test：mock LLM responses 走完 intent → audit → checklist → subagent → audit 全流程，驗證 retry 邊界
- [ ] 8.2 撰寫 mock test 覆蓋 SUBAGENT_DISHONEST 連續兩次 → TERMINATE 路徑
- [ ] 8.3 撰寫 mock test 覆蓋 deterministic check FAIL → subagent 重寫 → PASS 路徑
- [ ] 8.4 撰寫 mock test 覆蓋 LLM 不可用 → TERMINATE 路徑（不再有 rule-based fallback）
- [ ] 8.5 在 `docs/DEV_CHECKLIST.md` 新增/更新本 change 的手動 E2E 驗證步驟（max_loops=2、確認 v0/v1 `.py` 不同、確認 backtest metrics 不同、確認 Planka 附件清單符合預期、確認 staging 保留與 `v{N}_audit.md` 上傳）

## 9. 文件與 migration

- [x] 9.1 更新 `.ai/skills/freqtrade/trade-strategy-freqtrade-implementation/SKILL.md`：說明每輪獨立 `.py` 目錄與 strategy_spec 快照的存在
- [x] 9.2 更新 `docs/superpowers/specs/2026-04-23-e2e-test-skill-design.md`：補上 revise v2 的 audit log / staged artifacts / strategy snapshot 驗證要求；若未來 `.ai/skills/e2e-test/SKILL.md` 建立，需同步落實
- [x] 9.3 在 `docs/AGENTIC_RESEARCH_SOP_ZH.md` 加入 revise pipeline 流程圖（Stage A-E）
- [x] 9.4 撰寫 `openspec/changes/revise-pipeline-checklist-audit/MIGRATION.md`：說明部署順序、feature flag、rollback 策略

## 10. Feature flag 與 rollout

- [ ] 10.1 在 `app/workflow/executing_step.py` 或 `app/freqtrade/steps.py` 入口讀取 `REVISE_PIPELINE_VERSION`，預設 `v1`，非法值 fallback `v1` + log warning
- [ ] 10.2 dispatch 第一次進入 revise 時將決定的版本寫入 `projects.config.revise_pipeline_version`，後續輪次以該欄位為準（避免中途切換）
- [ ] 10.3 v1 路徑保留現有 `revise.txt` + `revise_validate.txt` 流程；v2 路徑走 task 3 的新 orchestrator
- [ ] 10.4 加 test 覆蓋三種 flag scenario：未設值 → v1；顯式 v2 → 走新流程；project 已有 v2 記錄 + flag 改 v1 → 仍走 v2
- [ ] 10.5 在測試 project 設 `REVISE_PIPELINE_VERSION=v2` 跑完整 `max_loops=2` 迴圈，驗證 v0/v1 backtest metrics 不同（核心驗收條件）
- [ ] 10.6 觀察 1~2 個 production project 完整跑完，確認沒有高頻 SUBAGENT_DISHONEST、無 promote 半成品錯誤
- [ ] 10.7 將 flag 預設改為 v2（修改 default 值並更新文件）；保留 v1 程式碼路徑至少一個 release
- [ ] 10.8 移除 v1 程式碼路徑與舊 prompts；archive 此 change：`openspec archive revise-pipeline-checklist-audit`
