## ADDED Requirements

### Requirement: 套件根目錄為 `app`
應用程式 Python 套件的根目錄 SHALL 為 `app/`。所有內部模組的 import 路徑 SHALL 以 `from app.` 開頭。`framework/` 目錄 SHALL NOT 存在。

#### Scenario: 正常 import
- **WHEN** 任何模組執行 `from app.api.server import app`
- **THEN** import 成功，無 ModuleNotFoundError

#### Scenario: 舊路徑不存在
- **WHEN** 執行 `from framework.workflow import dispatch_step`
- **THEN** 拋出 ModuleNotFoundError

### Requirement: 外部系統接口集中在 `app/clients/`
MinIO 與 Planka 的接口模組 SHALL 位於 `app/clients/` 下，以功能名稱命名。

#### Scenario: storage 模組路徑
- **WHEN** 執行 `from app.clients.storage import upload_artifact`
- **THEN** import 成功

#### Scenario: task_board 模組路徑
- **WHEN** 執行 `from app.clients.task_board import PlankaSink`
- **THEN** import 成功

#### Scenario: 舊廠商命名路徑不存在
- **WHEN** 執行 `from app.minio_client import upload_artifact`
- **THEN** 拋出 ModuleNotFoundError

### Requirement: LLM 模組集中在 `app/llm/`
LLM target 解析與 preflight 檢查 SHALL 位於 `app/llm/` subpackage。

#### Scenario: llm.target import
- **WHEN** 執行 `from app.llm.target import get_llm_target`
- **THEN** import 成功

#### Scenario: llm.preflight import
- **WHEN** 執行 `from app.llm.preflight import preflight_check`
- **THEN** import 成功

### Requirement: Freqtrade 回測模組位於 `app/freqtrade/`
所有 freqtrade 相關模組 SHALL 位於 `app/freqtrade/`。`quant_alpha/` 目錄 SHALL NOT 存在。

#### Scenario: backtest import
- **WHEN** 執行 `from app.freqtrade.backtest import run_backtest_is_oos`
- **THEN** import 成功

#### Scenario: steps import（原 plugin）
- **WHEN** 執行 `from app.freqtrade.steps import plan_step`
- **THEN** import 成功，`plan_step` 為 callable function（非 class method）

#### Scenario: cli module path
- **WHEN** 執行 `python -m app.freqtrade.cli backtest --help`
- **THEN** 命令執行成功，顯示 help 文字

### Requirement: Workflow 不使用 Plugin 抽象層
`app/workflow.py` SHALL 直接 import `app.freqtrade.steps` 的 module-level functions。`QuantAlphaPlugin` class SHALL NOT 存在。

#### Scenario: dispatch_step 不實例化 plugin
- **WHEN** `dispatch_step(project_id, db_url, sink)` 被呼叫
- **THEN** 不建立任何 Plugin 物件，直接呼叫 step function

#### Scenario: step functions 無 plugin 參數
- **WHEN** 檢視 `_run_plan`、`_run_implement`、`_run_test`、`_run_analyze`、`_run_summarize` 的函數簽名
- **THEN** 均不含 `plugin` 參數
