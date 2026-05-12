## Why

當 `dispatch_step` 或 `spec_review_step` 發生錯誤，開發者必須手動從 IDE console 複製錯誤文字、再逐步提供 DB 狀態給 Claude，才能開始診斷——這個流程重複且耗時。目標是讓錯誤發生的當下，系統自動將 Claude 所需的完整 context 寫入一個檔案，讓開發者只需一個指令即可開始診斷。

## What Changes

- `app/workflow/executing_step.py`：在 `dispatch_step` 的 except 區塊新增 `_write_error_report()` 呼叫
- `app/workflow/spec_review_step.py`：在 `run()` 的兩個 except 區塊新增相同呼叫
- 新增 `app/workflow/error_report.py`：共用的 `write_error_report()` helper，寫出 `artifacts/errors/last_error.txt`
- 新增 `.claude/skills/debug-card/SKILL.md`：定義 `/debug-card [card_id]` 指令的行為，讀取 `last_error.txt` 並查詢 DB，將完整 context 呈現給 Claude

## Capabilities

### New Capabilities

- `error-report-writer`：錯誤發生時自動將結構化報告寫入 `artifacts/errors/last_error.txt`，包含 project_id、card_id、Planka URL、workflow_step、spec 內容、完整 traceback 及關鍵環境變數
- `debug-card-skill`：Claude Code skill（`/debug-card [card_id]`），無參數時讀取 `last_error.txt` 自動取得 project context；有 card_id 時查 DB 取得 project_id，再呈現完整診斷資訊供 Claude 分析

### Modified Capabilities

（無現有 spec 需異動）

## Impact

- `app/workflow/executing_step.py`：在錯誤處理路徑加入 report writer 呼叫
- `app/workflow/spec_review_step.py`：在兩個 except 區塊加入 report writer 呼叫
- `app/workflow/error_report.py`（新增）：standalone helper，無外部依賴（僅 stdlib + dotenv + app.db.queries）
- `.claude/skills/debug-card/SKILL.md`（新增）：Claude Code project-level skill
- `artifacts/errors/`（新增目錄）：由 error_report.py 自動建立，不需 docker-compose 異動
