## ADDED Requirements

### Requirement: FAIL 後進入獨立的 REVISE step

當 `analyze_step` 判定結果為 FAIL 且尚未達到 `max_loops`，系統 SHALL 將 `workflow_step` 設為 `revise`（而非直接 `implement`），觸發 2-LLM 修訂流程。

`WorkflowStep` enum SHALL 新增 `REVISE = "revise"`。

#### Scenario: FAIL 觸發 REVISE step
- **WHEN** `analyze_step` 回傳 `FAIL` 且 `analyze_attempt < max_loops`
- **THEN** 系統 SHALL 設定 `workflow_step = revise`，下一次 dispatch 進入 `_run_revise`

#### Scenario: TERMINATE 不進入 REVISE
- **WHEN** `analyze_attempt >= max_loops`
- **THEN** 系統 SHALL 設定 `workflow_step = terminate`，不進入 REVISE

---

### Requirement: REVISE step 執行 2-LLM 串接修訂

`_run_revise` handler SHALL 依序執行兩次 LLM call：

1. **LLM 1**（`revise.txt` prompt）：讀取當前 `implementation_plan`、`last_reason`（失敗原因）、本輪 metrics → 產出修訂草稿 `revise_draft.json`
2. **LLM 2**（`revise_validate.txt` prompt）：讀取 `revise_draft.json` → 驗證合理性、補充考量 → 產出最終 `revised_params.json`

若 LLM 1 失敗，系統 SHALL fallback 至 rule-based 修訂（現有邏輯），跳過 LLM 2。
若 LLM 2 失敗，系統 SHALL 使用 LLM 1 的草稿作為最終結果。

#### Scenario: 兩個 LLM 均成功
- **WHEN** LLM 1 產出有效 `revise_draft.json`，LLM 2 產出有效 `revised_params.json`
- **THEN** `implementation_plan` 更新為 `revised_params.json` 的內容，`workflow_step` 設為 `implement`

#### Scenario: LLM 1 失敗
- **WHEN** LLM 1 執行失敗或未產出有效 JSON
- **THEN** 系統 SHALL 使用 rule-based fallback 產出修訂計畫，跳過 LLM 2，繼續 IMPLEMENT

#### Scenario: LLM 2 失敗
- **WHEN** LLM 1 成功但 LLM 2 執行失敗
- **THEN** 系統 SHALL 使用 `revise_draft.json` 作為最終計畫，log warning，繼續 IMPLEMENT

---

### Requirement: REVISE step 產出修訂方向文件並上傳 Planka

`_run_revise` handler SHALL 在 2-LLM 完成後產出 `v{N}_revised_direction.md`（N = `analyze_attempt`），
內容包含修訂原因與具體參數變動，並以 `upload_spec_attachment` 上傳至 Planka 卡片。

上傳失敗 SHALL 僅 log warning，不中斷 workflow。

#### Scenario: 修訂文件上傳成功
- **WHEN** 2-LLM 完成，`v{N}_revised_direction.md` 產出成功
- **THEN** 檔案上傳至 Planka 卡片附件，使用者可在 Planka 查看本輪修訂方向

#### Scenario: 上傳失敗不中斷流程
- **WHEN** `upload_spec_attachment` 拋出例外
- **THEN** 系統 SHALL log warning，`implementation_plan` 仍已更新，workflow 繼續進入 IMPLEMENT
