## Context

agentic-research 的 dispatch 迴圈（`executing_step.py`）目前有四個已知缺陷：

1. `max_loops` 的 Planka custom field 從未在 Executing 時讀入 DB，`_build_state` 永遠 fallback 到預設值 3
2. FAIL 路徑（`ANALYZE → IMPLEMENT`）沿用相同的 `implementation_plan` 重跑，`revise_step` 骨架存在但未接入 workflow
3. artifact 命名依賴 `loop_index`，而 `loop_index` 僅在 PASS → `summarize_step` 時遞增，FAIL 路徑所有輪均為 `loop_0_*`
4. `WorkflowStep.TERMINATE` 在 `_STEP_HANDLERS` 中無對應 handler，`terminate_summarize_step` 從未被呼叫

相關檔案：`app/api/server.py`、`app/workflow/executing_step.py`、`app/workflow/constants.py`、`app/freqtrade/steps.py`、`app/workflow/spec_review_step.py`

## Goals / Non-Goals

**Goals:**
- `max_loops` 在卡片移入 Executing 時正確寫入 DB
- FAIL 路徑引入 REVISE step：2-LLM 串接（draft → validate），產出修訂方向並上傳 Planka
- 每輪 artifact 以 `analyze_attempt` 計數命名，打包為 `v{N}_backtest.zip` 上傳
- spec review 附件保證 initial 先於 final 上傳
- TERMINATE 後 LLM 生成跨輪綜合報告並上傳 Planka

**Non-Goals:**
- 修改 DB schema 或 LangGraph state 結構
- 變更 spec review 的 LLM 評估邏輯
- 支援 PASS 路徑的多輪迭代（PASS 仍直接走 SUMMARIZE → DONE）
- mock 模式以外的 backtest 引擎修改

## Decisions

### D1：max_loops 寫入時機 — webhook handler 而非 dispatch 內部

**選擇**：在 `server.py` 的 Executing webhook handler 中，呼叫 `planka_client.read_card_custom_fields(card_id)` 讀取 `max_loops`，以 `merge_config` 寫入 DB 後再 dispatch。

**理由**：dispatch 層（`executing_step.py`）不應持有 Planka client 參考；card_id 在 webhook payload 中已存在，此時寫入最自然。`_build_state` 無需修改，仍從 `cfg.get("max_loops")` 讀取。

**捨棄的替代方案**：在 `_build_state` 內注入 planka_client — 會讓 dispatch 層耦合 Planka，違反現有職責分離。

---

### D2：REVISE step — 獨立 WorkflowStep，串接兩個 LLM call

**選擇**：新增 `WorkflowStep.REVISE`，`_ANALYZE_NEXT_STEP[FAIL]` 改指向 `REVISE`。`_run_revise` handler 在 `executing_step.py` 中呼叫改寫後的 `revise_step`（兩次循序 LLM call）。

```
FAIL
 └─ REVISE
     ├─ LLM 1 (revise.txt)     → revise_draft.json
     ├─ LLM 2 (revise_validate.txt) → revised_params.json
     ├─ 產出 v{N}_revised_direction.md
     ├─ upload to Planka
     └─ 寫入 implementation_plan → 進入 IMPLEMENT
```

**理由**：使用兩次循序 `_call_llm`（與現有 `revise_step` 相同的呼叫方式），不引入 `llm_eval.evaluate()` 的狀態機複雜度；`revise_validate.txt` 接收 LLM 1 的草稿作為輸入，責任明確。

**捨棄的替代方案**：在 `_run_implement` 偵測 FAIL 再 re-plan — 混合職責，不易測試。使用 `llm_eval.evaluate()` — 過重，revise 不需要 outcome 判別邏輯。

---

### D3：artifact 計數器 — 使用 analyze_attempt，不引入新欄位

**選擇**：`_mock_implement_result` 與 `_real_implement` 改為接收 `analyze_attempt`（從 state 讀取）作為本輪編號，命名格式 `v{N}_*`（N = analyze_attempt，0-indexed）。

**理由**：`analyze_attempt` 已在 state 中且在 FAIL 路徑正確遞增；引入新的 `iteration_index` 欄位只是重複這個語意。PASS 路徑首輪的 `analyze_attempt` 為 0，命名為 `v0_*`，語意一致。

**zip 打包時機**：在 `_run_analyze` 判定 result 後、呼叫 `_merge_state` 前，將本輪 implement 產出的新 artifacts 打包為 `v{N}_backtest.zip`，再呼叫 `sink.upload_bytes_attachment`。

---

### D4：spec review 上傳排序 — 明確優先順序函式

**選擇**：`_upload_work_dir` 改用 `sorted(..., key=_upload_priority)` 排列，priority 函式回傳：`reviewed_spec_initial.md` → 0，`reviewed_spec_final.md` → 1，其他 → 2（再按檔名字母排序）。

**理由**：最小改動，行為可預期，不依賴 OS 檔案系統排序。

---

### D5：TERMINATE handler — 呼叫既有 terminate_summarize_step，補上 Planka 上傳

**選擇**：在 `_STEP_HANDLERS` 加入 `WorkflowStep.TERMINATE: _run_terminate_summarize`。handler 呼叫 `terminate_summarize_step(state)`，取得 report markdown，上傳為 `v{loop_index}_{max_loops}_summary_report.md`，移卡至 Review。

**理由**：`terminate_summarize_step` 邏輯已完整，只需接線與上傳；report 命名帶版本號讓使用者在 Planka 附件中能直接識別是哪一組 (loop_index, max_loops) 的結果。

## Risks / Trade-offs

**[R1] analyze_attempt 在 PASS 路徑的語意**
→ PASS 路徑的首輪 analyze_attempt=0，命名 `v0_backtest.zip`，後續若再開新研究（新 project）仍從 0 開始，不造成衝突。但若同一 project 連續 PASS 多輪（loop_index 遞增），analyze_attempt 不重置，zip 命名為 `v0_`, `v1_`... 跨輪連續編號，語意與「第 N 輪回測」一致。可接受。

**[R2] LLM 2 對 LLM 1 草稿的依賴**
→ 若 LLM 1 輸出格式錯誤，LLM 2 可能無法補充，fallback 為 rule-based revise（現有邏輯保留）。revise_validate.txt 需明確規範輸入格式。

**[R3] Planka upload 在 REVISE step 失敗**
→ upload 失敗僅 log warning，不阻斷 workflow；revised_params.json 已寫入 DB，IMPLEMENT 仍能繼續執行。使用者可從 Planka comment 看到失敗警告。

**[R4] max_loops 空值或非整數**
→ `int(cfg.get("max_loops") or 3)` 現有 fallback 已處理；webhook handler 讀取時加 try/except，無效值不寫入 DB（保留既有值或 fallback 3）。

## Migration Plan

1. 部署新版本前無需 DB migration（schema 不變）
2. 進行中的 project（workflow_step = implement/test/analyze）不受影響：REVISE step 只在下一次 FAIL 後觸發
3. 舊有 `loop_0_*` 命名的 artifact 繼續存在，不需清理（新輪使用 `v{N}_*` 命名空間不衝突）
4. rollback：還原 `_ANALYZE_NEXT_STEP[FAIL]` 為 `WorkflowStep.IMPLEMENT` 即可退回舊行為

## Open Questions

- `revise_validate.txt` prompt 的輸入格式是否需要在 spec 中明確規範，或由 implementation 自行決定？
- `v{N}_revised_direction.md` 是否同時也需要更新 Planka 卡片的 `spec.raw_md`（即讓下一輪 plan_step 讀到修訂後的 spec）？目前方案是修訂結果只進 `implementation_plan`，不更新 spec。
