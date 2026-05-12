## 1. Error Report Writer

- [x] 1.1 建立 `app/workflow/error_report.py`：實作 `write_error_report(project_id, exc, step, db_url)` — 查 DB 取得 card_id、config、spec，收集 env vars，寫出 `artifacts/errors/last_error.txt`；所有 DB 查詢以 try/except 包覆，寫入失敗只記 warning
- [x] 1.2 在 `app/workflow/executing_step.py` 的 `dispatch_step` except 區塊加入 `write_error_report()` 呼叫（在現有 logger + Planka comment 之後）
- [x] 1.3 在 `app/workflow/spec_review_step.py` 的 `run()` 兩個 except 區塊（`LLMEvaluationError`、`Exception`）各加入 `write_error_report()` 呼叫

## 2. Debug Card Skill

- [x] 2.1 建立 `.claude/skills/debug-card/SKILL.md`：定義 `/debug-card [card_id]` 行為 — 無參數時讀取 `artifacts/errors/last_error.txt` 解析 project_id；有 card_id 時查 DB 反查 project_id；查 DB 取最新 config；整合呈現完整診斷 context
- [x] 2.2 在 `.claude/CLAUDE.md` 登記 `debug-card` skill 的觸發說明，確保 `/debug-card` 能被 Claude 識別
