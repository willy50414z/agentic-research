## ADDED Requirements

### Requirement: 每輪 artifact 以 analyze_attempt 計數命名

implement step 產出的所有 artifact 檔案 SHALL 以 `v{N}_` 為前綴，N 為當輪的 `analyze_attempt` 值（0-indexed）。

現有以 `loop_{loop_index}_` 為前綴的命名 SHALL 改為 `v{N}_`（例如 `loop_0_train.json` → `v0_train.json`）。

`analyze_attempt` 在 FAIL 路徑每輪遞增，確保各輪 artifact 不互相覆蓋。

#### Scenario: 第 0 輪 implement
- **WHEN** `analyze_attempt = 0`，`implement_step` 執行
- **THEN** 產出檔案命名為 `v0_train.json`（mock 模式）或 `v0_is.json`、`v0_oos.json`（real 模式）

#### Scenario: FAIL 後第 1 輪 implement
- **WHEN** 第 0 輪 FAIL，`analyze_attempt` 遞增為 1，REVISE 完成後進入 implement
- **THEN** 產出檔案命名為 `v1_train.json`，不覆蓋 `v0_train.json`

#### Scenario: PASS 路徑命名一致
- **WHEN** 第 0 輪 PASS（`analyze_attempt = 0`）
- **THEN** artifact 命名為 `v0_*`，與 FAIL 路徑語意一致

---

### Requirement: 每輪 artifact 打包為 v{N}_backtest.zip 上傳 Planka

`_run_analyze` 判定本輪結果後，SHALL 將當輪 implement 產出的所有 artifact 打包為 `v{N}_backtest.zip`（N = `analyze_attempt`），以 `upload_bytes_attachment` 上傳至 Planka 卡片。

打包後，個別 artifact 檔案 SHALL NOT 再單獨上傳（避免 Planka 附件過多）。

若 zip 打包或上傳失敗，SHALL 僅 log warning，不中斷 analyze 結果判定與後續 step 轉換。

#### Scenario: 正常打包上傳
- **WHEN** implement 產出 `v0_train.json`，analyze 完成
- **THEN** `v0_backtest.zip` 上傳至 Planka，zip 內包含 `v0_train.json`

#### Scenario: 多個 artifact 打包
- **WHEN** real 模式 implement 產出 `v0_is.json`、`v0_oos.json`、`v0_trades.json`、`v0_report.html`
- **THEN** 四個檔案均包含於 `v0_backtest.zip` 中

#### Scenario: 打包失敗不中斷流程
- **WHEN** zip 打包過程中拋出例外
- **THEN** 系統 SHALL log warning，analyze 結果（PASS/FAIL/TERMINATE）的 step 轉換正常執行

---

### Requirement: spec review 附件以確定順序上傳

`_upload_work_dir` SHALL 保證 `reviewed_spec_initial.md` 在 `reviewed_spec_final.md` 之前上傳；其餘檔案以檔名字母順序排列於後。

#### Scenario: 兩個 reviewed_spec 檔案均存在
- **WHEN** work_dir 中同時有 `reviewed_spec_initial.md` 與 `reviewed_spec_final.md`
- **THEN** initial 先上傳，final 後上傳，Planka 附件列表中 initial 的 `createdAt` 早於 final

#### Scenario: 僅有 reviewed_spec_final.md
- **WHEN** work_dir 中只有 `reviewed_spec_final.md`
- **THEN** 直接上傳，無排序問題
