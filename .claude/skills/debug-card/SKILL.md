---
name: debug-card
description: >
  讀取最新錯誤報告或依 card_id 查詢 DB，將完整診斷 context 呈現給 Claude，讓 Claude
  無需再詢問即可直接開始分析根本原因。
  觸發時機：使用者輸入 /debug-card 或 /debug-card {card_id}。
---

# Debug Card

## 概述

當 `dispatch_step` 或 `spec_review_step` 發生錯誤時，系統會自動寫出
`artifacts/errors/last_error.txt`，其中包含 project_id、card_id、Planka URL、
workflow_step、spec 欄位、traceback 及關鍵 env vars。

此 skill 負責將這些 context 整合呈現，讓 Claude 可以直接開始診斷。

---

## 步驟

### 1. 決定來源路徑

**無參數 (`/debug-card`)**：
- 讀取 `artifacts/errors/last_error.txt`
- 若檔案不存在 → 告知使用者：
  > 找不到 `artifacts/errors/last_error.txt`。此檔案在工作流步驟失敗時自動產生。
  > 請先觸發一次錯誤，或改用 `/debug-card {card_id}` 直接指定卡片。
- 若存在 → 從第 `project_id:` 行解析 project_id（格式：`project_id:         <value>`）

**有 card_id 參數 (`/debug-card {card_id}`)**：
- 執行以下 Python 查詢，以 `planka_card_id` 欄位反查 project_id：
  ```python
  import os, json
  import psycopg
  db_url = os.environ["DATABASE_URL"]
  with psycopg.connect(db_url) as conn:
      with conn.cursor() as cur:
          cur.execute(
              "SELECT id FROM projects WHERE config->>'planka_card_id' = %s",
              ("{card_id}",),
          )
          row = cur.fetchone()
  project_id = row[0] if row else None
  print(project_id)
  ```
- 若找不到 → 告知使用者：
  > 找不到 card_id `{card_id}` 對應的 project。請確認 card_id 是否正確。
- 若找到 → 繼續步驟 2

### 2. 查詢 DB 取得最新狀態

以 project_id 查詢：

```python
import os, json
import psycopg
db_url = os.environ["DATABASE_URL"]
with psycopg.connect(db_url) as conn:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, workflow_step, config FROM projects WHERE id = %s",
            ("{project_id}",),
        )
        row = cur.fetchone()
if row:
    result = {
        "project_id": row[0],
        "name": row[1],
        "workflow_step": row[2],
        "config": row[3],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
```

### 3. 呈現完整診斷 context

將以下所有資訊整合後輸出給 Claude：

```
=== 診斷 Context ===

project_id:         {project_id}
card_id:            {config.planka_card_id 或 "(未記錄)"}
Planka URL:         {PLANKA_API_URL}/cards/{card_id} 或 "(未知)"
workflow_step:      {workflow_step}
last_result:        {config.last_result}
last_reason:        {config.last_reason}
review_in_progress: {config.review_in_progress}

=== Spec 內容 ===
{config.spec 的完整 JSON，若為空則標示 "(empty)"}

=== last_error.txt 內容 ===
{last_error.txt 全文，若無參數路徑且檔案存在則貼出；有 card_id 路徑則嘗試讀取後貼出，不存在則標示 "(not available)"}
```

輸出後直接開始分析，無需再向使用者詢問額外資訊。
