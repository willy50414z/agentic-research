## Context

`dispatch_step`（`app/workflow/executing_step.py`）與 `spec_review_step`（`app/workflow/spec_review_step.py`）是兩個主要錯誤發生點。目前錯誤僅輸出至 stdout（IDE console）並透過 Planka comment 通知，開發者必須手動複製錯誤文字、再反覆提供 DB 狀態，才能讓 Claude 開始診斷。`planka_card_id` 已存於 `projects.config`，`workflow_step` 與 `spec` 也都可從 DB 即時取得。

## Goals / Non-Goals

**Goals:**
- 錯誤發生時，在 `artifacts/errors/last_error.txt` 自動寫出完整 context（project_id、card_id、Planka URL、workflow_step、spec、traceback、env）
- 建立 `/debug-card [card_id]` skill，讓 Claude 讀取 `last_error.txt`（或依 card_id 查詢 DB）後直接開始診斷
- 涵蓋兩個錯誤來源：`dispatch_step` 與 `spec_review_step`

**Non-Goals:**
- 不建立 log 聚合或遠端錯誤追蹤（Sentry、Datadog 等）
- 不保留歷史 error report（固定覆寫 `last_error.txt`）
- 不提供 Web UI 或 API endpoint 給 error report
- 不修改 docker-compose 或 logging 設定

## Decisions

**D1 — 單一共用 helper 模組（`app/workflow/error_report.py`）**
兩個錯誤來源（`dispatch_step`、`spec_review_step`）共用同一個 `write_error_report(project_id, exc, step, db_url)` 函式，而非各自重複實作。
替代方案：在各 module 內各自寫 — 拒絕，因為 context 收集邏輯（查 card_id、env）會重複。

**D2 — 固定覆寫 `last_error.txt`，不保留歷史**
每次錯誤都覆寫同一個檔案，讓使用者只需說「有錯誤」Claude 就知道看哪裡，不需提供檔名。
替代方案：每次寫 `<project_id>_<timestamp>.txt` — 拒絕，因為增加使用者負擔（要說檔名）。

**D3 — Skill 置於 `.claude/skills/debug-card/SKILL.md`**
與現有 `e2e-test`、`openspec-*` skills 同路徑，讓 `/debug-card` 在 Claude Code session 內自動可用。
替代方案：置於 `knowledge-base/` — 拒絕，該路徑的 skill 須手動透過 Skill tool 呼叫，無法成為 slash command。

**D4 — Skill 無參數時讀 `last_error.txt`，有 card_id 時查 DB**
無參數是最常用路徑（剛剛出錯），有 card_id 提供額外彈性（想診斷非最新錯誤）。
兩條路徑最終都查詢 DB 取得最新狀態，確保 context 不因 `last_error.txt` 過舊而失真。

**D5 — error_report.py 不丟例外**
寫入 `last_error.txt` 失敗時只記 warning，絕不讓 report writer 本身的錯誤蓋掉原始錯誤的處理流程。

## Risks / Trade-offs

- **`artifacts/errors/` 目錄不存在** → 由 `write_error_report()` 呼叫 `mkdir(parents=True, exist_ok=True)` 自動建立，不需額外設定
- **`last_error.txt` 被覆寫導致舊資訊遺失** → 可接受；歷史紀錄已存於 Planka comment 及 Python logger stdout
- **DB 在錯誤瞬間也不可用** → `write_error_report()` 所有 DB 查詢均有 try/except，無法取得的欄位標記為 `(unavailable)`，不影響其他欄位的寫出
- **Skill 描述的 Python inline 指令與未來重構脫鉤** → skill 只描述「查什麼」的意圖，實際 Python 指令在 apply 時對齊當前 codebase

## Migration Plan

無 breaking change，無需 migration。
新增檔案不影響現有功能；錯誤處理路徑的呼叫是附加的（原有 logger + Planka comment 保持不動）。

## Open Questions

（無）
