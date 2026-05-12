## ADDED Requirements

### Requirement: 以 llm_eval.evaluate() 統一所有 LLM 呼叫
所有 LLM 呼叫 SHALL 透過 `llm_eval.evaluate(target, purpose, outcomes)` 執行。不得直接調用 `LLMProviderFactory`、subprocess、或任何 LLM SDK。`llm_eval` 須以 `pip install -e E:\code\agent-cli\` 安裝。

#### Scenario: 正常 LLM 呼叫
- **WHEN** step 函式需要 LLM 判斷（如 analyze、spec review）
- **THEN** 呼叫 `evaluate()`，LLM 寫入 status file 信號 outcome，callback 接收 `JobResult` 並更新 state

#### Scenario: LLM subprocess 失敗
- **WHEN** LLM CLI 回傳非零 exit code 或 timeout
- **THEN** `on_exception` callback 被呼叫，系統移卡至 `Failed` 並在 Planka card 留 error comment

#### Scenario: LLM 未寫 status file
- **WHEN** LLM 執行完畢但未建立任何 `status_*` 檔案
- **THEN** `evaluate()` 拋出 `RuntimeError`，由 step 函式的 exception handler 捕捉並移卡至 `Failed`

### Requirement: LLM target 透過環境變數設定
系統 SHALL 讀取環境變數 `LLM_TARGET`（值為 `CLAUDE`、`GEMINI`、`CODEX`、`OPENCODE`、`COPILOT`）決定使用的 LLM CLI。無效值或未設定時，系統 SHALL 在啟動時記錄 error 並拒絕啟動。

#### Scenario: 有效 LLM_TARGET
- **WHEN** `LLM_TARGET=CLAUDE`
- **THEN** 所有 `evaluate()` 呼叫使用 `LLMTarget.CLAUDE`（即 `claude` CLI binary）

#### Scenario: 無效 LLM_TARGET
- **WHEN** `LLM_TARGET` 未設定或值不在支援清單內
- **THEN** 系統啟動時 preflight check 失敗，記錄 error，API 回應 503

### Requirement: Outcome 宣告式定義 LLM 可能的結果
每個 `evaluate()` 呼叫 SHALL 透過 `Outcome` 物件宣告所有可能的結果，包含：status 識別字、description（顯示給 LLM 的決策說明）、output_files（LLM 須寫入的檔案）、callback（結果處理函式）。

#### Scenario: analyze_node PASS/FAIL/TERMINATE routing
- **WHEN** `analyze_node()` 呼叫 `evaluate()`
- **THEN** Outcomes 包含 `pass`（output: `reason.txt`）、`fail`（output: `reason.txt`）、`terminate`（output: `reason.txt`），callback 將 `last_result` 與 `last_reason` 寫入 state updates dict

#### Scenario: spec review PASS/need_update routing
- **WHEN** spec review step 呼叫 `evaluate()`
- **THEN** Outcomes 包含 `pass`（output: `reviewed_spec.md`）、`need_update`（output: `questions.txt`），callback 決定移卡方向

### Requirement: 移除 LLMProviderFactory 與 tag_parser
系統 SHALL 不包含 `framework/llm_providers.py` 與 `framework/tag_parser.py`。任何對這兩個模組的 import 視為建構錯誤。

#### Scenario: 舊 provider 程式碼不存在
- **WHEN** 任何模組嘗試 `from framework.llm_providers import LLMProviderFactory`
- **THEN** ImportError（檔案已刪除）
