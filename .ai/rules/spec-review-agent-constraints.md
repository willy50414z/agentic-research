# Spec Review Agent Constraints

這些約束適用於所有 spec review agent（initial、refine、synthesize）。
agent 執行時必須優先遵守，凌駕一切習慣與預設行為。

## 禁止行為

- 禁止執行目錄掃描命令：Get-ChildItem、ls、dir、find
- 禁止讀取 `projects/` 目錄下的任何檔案
- 禁止讀取 prompt 未明確指定路徑以外的任何 spec、rules 或 markdown 檔案
- 禁止詢問任何問題；所有判斷直接執行，可合理推斷的欄位一律推斷並說明

## 必寫輸出協定

每次執行必須寫入以下檔案到 `{OUTPUT_DIR}`（且只寫到此目錄）：

| 角色 | 必寫 spec 檔案 | 狀態檔案（二擇一） |
|------|---------------|------------------|
| initial | `reviewed_spec_initial.md` | `status_pass.txt` 或 `status_need_update.txt` |
| refine | `reviewed_spec_final.md` | `status_pass.txt` 或 `status_need_update.txt` |
| synthesize | `reviewed_spec_final.md` | `status_pass.txt` 或 `status_need_update.txt` |

- `status_pass.txt`：無待釐清問題時寫入，內容為 `PASS`
- `status_need_update.txt`：有待釐清問題時寫入，每行一個問題
- 兩個狀態檔案不可同時存在

