## MODIFIED Requirements

### Requirement: 綜合報告以版本化命名上傳 Planka

`_run_terminate_summarize` handler SHALL 將報告以 `v{first_iter}_{last_iter}_summary_report.md` 命名，上傳至 Planka 卡片附件，並移卡至 Review。

命名規則：

- `first_iter` = 0（baseline 永遠是第 0 輪）
- `last_iter` = `analyze_attempt - 1`（最後一輪實際執行的 iteration index，0-indexed）

命名範例：

- 跑了 1 輪後 TERMINATE → `v0_0_summary_report.md`
- 跑了 2 輪後 TERMINATE → `v0_1_summary_report.md`
- 跑了 3 輪後 TERMINATE → `v0_2_summary_report.md`

`loop_index` SHALL NOT 用於檔名生成（`loop_index` 在 FAIL 路徑永遠為 0，無法反映實際執行範圍）。

#### Scenario: max_loops=2 跑滿後 TERMINATE
- **WHEN** `analyze_attempt = 2`（v0、v1 各跑一次後 TERMINATE）
- **THEN** 報告命名為 `v0_1_summary_report.md`，Planka 附件出現該檔，卡片移至 Review

#### Scenario: 第一輪 LLM 不可用直接 TERMINATE
- **WHEN** `analyze_attempt = 1`（v0 跑完後 revise 階段 LLM 失敗 TERMINATE）
- **THEN** 報告命名為 `v0_0_summary_report.md`

#### Scenario: 報告 post_comment 與檔名一致
- **WHEN** terminate_summarize 完成、post_comment 引用報告檔名
- **THEN** 評論中的檔名與實際上傳檔名一致（皆為 `v{first_iter}_{last_iter}_summary_report.md`）

#### Scenario: 上傳失敗不中斷卡片移動
- **WHEN** `upload_spec_attachment` 拋出例外
- **THEN** 系統 SHALL log warning，卡片仍 SHALL 移至 Review；本地報告檔案保留

#### Scenario: workflow_step 更新為 done
- **WHEN** `_run_terminate_summarize` 完成（不論上傳是否成功）
- **THEN** `workflow_step` SHALL 設為 `done`，防止 dispatch loop 重複執行
