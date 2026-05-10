## ADDED Requirements

### Requirement: max_loops 同步時序與驗證

`max_loops` 從 Planka 卡片 custom field 同步至 DB `projects.config` 的時序 SHALL 嚴格保證在 dispatch 內第一次 `_build_state` 之前完成：

1. `dispatch_bg` 入口 SHALL 先讀 card custom fields，若有 `max_loops` 則立刻 `merge_config` 寫 DB
2. `merge_config` 完成後 SHALL 重新 `get_project` 拿到最新 cfg，再呼叫 `dispatch_step`
3. `_build_state` 讀 `cfg.get("max_loops")` 時 SHALL log 實際讀到的值

`dispatch_bg` SHALL 額外輸出 sanity-check log，記錄三組值：card 上的原始值、merge_config 寫入的值、`_build_state` 讀到的值。三者不一致時 log warning（但不阻擋 workflow 繼續，避免 card schema 異常時整個 pipeline 卡死）。

#### Scenario: 正常同步路徑
- **WHEN** card `max_loops = 2`，dispatch_bg 觸發
- **THEN** log 序列出現「card raw=2 → merged=2 → state=2」三條一致記錄；後續 `_run_analyze` 用 max_loops=2 判定 TERMINATE 條件

#### Scenario: card 未設值
- **WHEN** card `max_loops` custom field 不存在或為空字串
- **THEN** 系統 SHALL 不寫 DB，`_build_state` fallback 預設值 3，log 序列為「card raw=null → state=3 (default)」

#### Scenario: 三值不一致觸發 warning
- **WHEN** card raw=2 但 `_build_state` 讀到 3（推測為 race condition 或舊值殘留）
- **THEN** 系統 SHALL log warning「max_loops mismatch: card=2 state=3」；workflow 繼續使用 state 值，不阻擋

#### Scenario: 非整數值
- **WHEN** card `max_loops` 值為非整數字串（例如 `"abc"`）
- **THEN** 系統 SHALL 捕捉例外、log warning、不寫 DB，`_build_state` 用 DB 既有值或預設值 3
