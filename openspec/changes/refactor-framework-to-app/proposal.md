## Why

`framework/` 是一個毫無語意的套件名稱，內部混放了基礎設施、外部系統接口、業務邏輯與 LangGraph 移除後遺留的 plugin 抽象層，導致程式結構難以推理、import 路徑無法傳遞意圖。現在是清理的時機，因為 LangGraph 已移除、plugin 架構已無必要，整體規模仍小、重構成本低。

## What Changes

- **BREAKING** `framework/` 套件改名為 `app/`，所有 `from framework.xxx` 改為 `from app.xxx`
- 外部系統接口（MinIO、Planka）集中到 `app/clients/`，以功能名稱取代廠商名稱
  - `minio_client.py` → `app/clients/storage.py`
  - `planka.py` → `app/clients/task_board.py`
- LLM 相關模組歸組為 `app/llm/` subpackage
  - `llm_target.py` → `app/llm/target.py`
  - `llm_preflight.py` → `app/llm/preflight.py`
- `quant_alpha/` 改名為 `freqtrade/`，反映真實內容（全為 freqtrade 回測工具）
  - `freqtrade_cli.py` → `cli.py`（folder 已具 freqtrade 語意，前綴冗餘）
  - `freqtrade_runner.py` → `runner.py`
  - `plugin.py` → `steps.py`（移除 `QuantAlphaPlugin` class，改為 module-level functions）
- `workflow.py` 移除 plugin 抽象層，直接 import `freqtrade.steps` 中的函數
- `prompts/quant_alpha/` → `prompts/freqtrade/`
- Spec review prompts 清除已過時的 "Plugin" 欄位描述

## Capabilities

### New Capabilities

無新功能。本次為純結構性重構。

### Modified Capabilities

- `package-layout`: 套件根目錄從 `framework` 改為 `app`，子模組位置全面調整

## Impact

- **Python imports**：所有 `from framework.xxx` → `from app.xxx`，涵蓋 `framework/` 自身、`tests/`、`main.py`（約 45 處）
- **`tests/test_freqtrade_integration.py`**：大量 `from framework.quant_alpha` → `from app.freqtrade`
- **`workflow.py`**：移除 `QuantAlphaPlugin()` 實例化及所有 `plugin` 函數參數，改為直接 import step functions
- **LLM prompts**：`prompts/spec_review/*.txt` 移除 "Plugin 欄位" 相關描述；`prompts/quant_alpha/` 目錄改名為 `prompts/freqtrade/`
- **`python -m` 入口**：`freqtrade_cli.py` docstring 中記載的 `python -m framework.quant_alpha.freqtrade_cli` 更新為 `python -m app.freqtrade.cli`
- **無 API、DB schema、docker-compose 變動**
