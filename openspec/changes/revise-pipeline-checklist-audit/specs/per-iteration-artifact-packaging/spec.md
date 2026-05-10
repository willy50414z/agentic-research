## MODIFIED Requirements

### Requirement: 每輪 artifact 打包為 v{N}_backtest.zip 上傳 Planka

`_run_analyze` 判定本輪結果後，SHALL 將當輪 implement 產出的所有 artifact 打包為 `v{N}_backtest.zip`（N = `analyze_attempt`），以 `upload_bytes_attachment` 上傳至 Planka 卡片。

`is_zip` 與 `oos_zip` 類型的 artifact SHALL NOT 由 `_upload_new_artifacts` 個別上傳；它們僅作為 `v{N}_backtest.zip` 的內容物存在於卡片附件中。其他類型（is_result、oos_result、trades、signals、report 等）的 artifact 同樣不再單獨上傳。

若 zip 打包或上傳失敗，SHALL 僅 log warning，不中斷 analyze 結果判定與後續 step 轉換。

#### Scenario: 正常打包上傳，無重複附件
- **WHEN** real 模式 implement 產出 `v0_is.json`、`v0_oos.json`、`v0_trades.json`、`v0_signals.json`、`v0_report.html`、`v0_is.zip`、`v0_oos.zip`，analyze 完成
- **THEN** Planka 卡片附件僅出現 `v0_backtest.zip` 一個（內含上述七個檔案），不出現獨立的 `v0_is.zip` 或 `v0_oos.zip`

#### Scenario: implement 階段不個別上傳 is/oos zip
- **WHEN** `_run_implement` 完成、artifacts 列表含 `is_zip` 與 `oos_zip` 類型的條目
- **THEN** `_upload_new_artifacts` SHALL 過濾 `type ∈ {is_zip, oos_zip, is_result, oos_result, trades, signals, report}`，不個別上傳這些檔案至 Planka

#### Scenario: 打包失敗不中斷流程
- **WHEN** zip 打包過程中拋出例外
- **THEN** 系統 SHALL log warning，analyze 結果（PASS/FAIL/TERMINATE）的 step 轉換正常執行
