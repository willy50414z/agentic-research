## 1. 目錄搬移與重命名

- [x] 1.1 將 `framework/` 整體複製為 `app/`（保留原目錄直到 import 全部更新後再刪除）
- [x] 1.2 在 `app/` 內建立 `clients/` 子目錄，並加入 `__init__.py`
- [x] 1.3 在 `app/` 內建立 `llm/` 子目錄，並加入 `__init__.py`
- [x] 1.4 將 `app/minio_client.py` 移至 `app/clients/storage.py`
- [x] 1.5 將 `app/planka.py` 移至 `app/clients/task_board.py`
- [x] 1.6 將 `app/llm_target.py` 移至 `app/llm/target.py`
- [x] 1.7 將 `app/llm_preflight.py` 移至 `app/llm/preflight.py`
- [x] 1.8 將 `app/quant_alpha/` 整體改名為 `app/freqtrade/`
- [x] 1.9 將 `app/freqtrade/freqtrade_cli.py` 改名為 `app/freqtrade/cli.py`
- [x] 1.10 將 `app/freqtrade/freqtrade_runner.py` 改名為 `app/freqtrade/runner.py`
- [x] 1.11 將 `app/prompts/quant_alpha/` 改名為 `app/prompts/freqtrade/`

## 2. Plugin 抽象層移除（`steps.py` 轉換）

- [x] 2.1 將 `app/freqtrade/plugin.py` 改名為 `app/freqtrade/steps.py`
- [x] 2.2 移除 `QuantAlphaPlugin` class 定義，將 `plan_node`、`implement_node`、`test_node`、`analyze_node`、`summarize_node` 轉為 module-level functions（移除 `self` 參數）
- [x] 2.3 更新 `steps.py` 內部對 `_real_implement`、`_mock_*` 等 private method 的呼叫（去掉 `self.` 前綴，改為 module-level functions 或直接呼叫）
- [x] 2.4 更新 `app/workflow.py`：移除 `from .quant_alpha.plugin import QuantAlphaPlugin` 和 `plugin = QuantAlphaPlugin()`，改為 `from .freqtrade.steps import plan_step, implement_step, test_step, analyze_step, summarize_step`
- [x] 2.5 更新 `app/workflow.py` 中所有 `_run_*` 函數：移除 `plugin` 參數，將 `plugin.xxx_node(state)` 改為直接呼叫 step function

## 3. 內部 import 路徑更新（`app/` 內部）

- [x] 3.1 更新 `app/api/server.py`：所有 `from framework.xxx` → `from app.xxx`，`from framework.planka import PlankaSink` → `from app.clients.task_board import PlankaSink`，`from framework.llm_target` → `from app.llm.target`
- [x] 3.2 更新 `app/workflow.py`：所有 `from .db.xxx` 路徑確認正確（相對 import 應不受影響）
- [x] 3.3 更新 `app/spec_review.py`：`from .llm_target` → `from .llm.target`
- [x] 3.4 更新 `app/llm/preflight.py`（原 `llm_preflight.py`）：調整任何自我引用的 import 路徑
- [x] 3.5 更新 `app/clients/task_board.py`（原 `planka.py`）：`from framework.db.xxx` → `from app.db.xxx`
- [x] 3.6 更新 `app/freqtrade/backtest.py`：`from framework.quant_alpha.xxx` → `from app.freqtrade.xxx`（`config_generator`、`runner`）
- [x] 3.7 更新 `app/freqtrade/cli.py`（原 `freqtrade_cli.py`）：`from framework.quant_alpha.xxx` → `from app.freqtrade.xxx`；更新 docstring 中的 `python -m` 路徑為 `python -m app.freqtrade.cli`
- [x] 3.8 更新 `app/freqtrade/steps.py`（原 `plugin.py`）：`from framework.quant_alpha.xxx` → `from app.freqtrade.xxx`；`_PROMPTS_DIR` 路徑調整以指向 `app/prompts/freqtrade/`

## 4. 外部 import 路徑更新

- [x] 4.1 更新 `main.py`：`from framework.api.server import app` → `from app.api.server import app`
- [x] 4.2 更新 `tests/test_freqtrade_integration.py`：所有 `from framework.quant_alpha.xxx` → `from app.freqtrade.xxx`（約 20 處）
- [x] 4.3 更新 `tests/test_workflow.py`：`from framework import workflow as wf` → `from app import workflow as wf`；`from framework.spec_review` → `from app.spec_review`
- [x] 4.4 更新 `tests/conftest.py`：調整任何 `framework` 參照

## 5. Prompt 文字清理

- [x] 5.1 更新 `app/prompts/spec_review/spec_agent_initial.txt`：移除「Plugin（固定填入 `quant_alpha`）」相關描述
- [x] 5.2 更新 `app/prompts/spec_review/spec_agent_refine.txt`：移除「Plugin（固定為 `quant_alpha`）」相關描述
- [x] 5.3 更新 `app/prompts/spec_review/spec_agent_synthesize.txt`：移除「確認 Plugin 欄位已填入 `quant_alpha`」描述

## 6. 舊目錄清除與驗證

- [x] 6.1 確認 `grep -rn "from framework\|import framework" --include="*.py"` 無任何結果（venv 除外）
- [x] 6.2 確認 `grep -rn "quant_alpha" --include="*.py" --include="*.txt"` 無任何結果（venv 除外）
- [x] 6.3 刪除原 `framework/` 目錄
- [x] 6.4 執行 `pytest` 確認全部測試通過
