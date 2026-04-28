# Freqtrade 真實回測整合設計

## 對需求的理解

將 `projects/quant_alpha/backtest.py` 從 stub 替換為真實 Freqtrade CLI 呼叫，透過 `BACKTEST_MODE` 環境變數切換 mock/real 行為。LLM 可依策略改善計畫選擇執行模式（基本回測、參數優化、交叉測試）。每次 loop 產出完整分析資料供後續 LLM 參考，並擇要上傳 Planka。

---

## 範疇

對應 `docs/DEV_CHECKLIST.md` 的 TODO A（T-A01、T-A02、T-A03）。

---

## 調用鏈（重要）

```
implement_node
  └─ subprocess → freqtrade_cli.py  (argument parsing, subcommand dispatch)
                      └─ import → freqtrade_runner.py  (subprocess to freqtrade CLI)
                      └─ import → result_parser.py

backtest.py
  └─ import → freqtrade_runner.py  ← 直接呼叫，不走 subprocess
  └─ import → result_parser.py
```

`freqtrade_cli.py` 是 CLI entry point（供外部 subprocess 呼叫），內部 import `freqtrade_runner.py`。
`backtest.py` 直接 import `freqtrade_runner.py`，不經由 CLI 入口，避免雙層 subprocess。

---

## IS/OOS 執行流程

```
backtest.py（real mode）
  1. config_generator.py 生成 config.json（從 spec）
  2. run_freqtrade_backtest(timerange=IS_range)  → is.zip
     IS_range 來自 spec["data"]["train_period"]（格式 YYYYMMDD-YYYYMMDD）
  3. run_freqtrade_backtest(timerange=OOS_range) → oos.zip
     OOS_range 來自 spec["data"]["test_period"]
  4. result_parser.parse(is.zip)  → loop_N_is.json
  5. result_parser.parse(oos.zip) → loop_N_oos.json
  6. 生成 loop_N_trades.json、loop_N_signals.json、loop_N_report.html
```

---

## 架構

### 新增檔案

```
projects/quant_alpha/
  freqtrade_cli.py      # CLI 入口：subcommand dispatch（backtest / hyperopt / cross_test）
  freqtrade_runner.py   # subprocess 封裝：呼叫 freqtrade CLI、收集 .zip
  config_generator.py   # 從 spec dict 動態生成 Freqtrade config.json
  result_parser.py      # 解析 .zip，生成指標 JSON + 交易紀錄 + HTML 報告
```

### 修改檔案

```
projects/quant_alpha/
  backtest.py           # 移除 stub，改為呼叫 freqtrade_runner + result_parser
  plugin.py             # implement_node / test_node 加 BACKTEST_MODE mock gate
framework/prompts/quant_alpha/
  analyze.txt           # 加入 IS/OOS 雙組指標變數
  plan.txt              # 加入 run_mode 選擇說明（backtest/hyperopt/cross_test）
tests/
  test_freqtrade_integration.py  # 新增，標記 @pytest.mark.freqtrade_real
```

---

## 工作目錄規則

每次 implement_node 開始時建立**持久化**隔離工作目錄（不清理，供重複執行）：

```
artifacts/.llm_io/{loop_index}_{timestamp}/
  strategies/             # LLM 生成的策略 .py
  config.json             # 從 spec 動態生成
  backtest_results/       # Freqtrade .zip 輸出
  loop_N_is.json          # IS 指標摘要（上傳 Planka）
  loop_N_oos.json         # OOS 指標摘要（上傳 Planka）
  loop_N_trades.json      # 逐筆交易紀錄（上傳 Planka）
  loop_N_signals.json     # 訊號數據
  loop_N_report.html      # 完整 HTML 報告（上傳 Planka）
  analyze_result.txt      # PASS / FAIL / TERMINATE

artifacts/
  user_data/data/         # 共用歷史資料（跨 loop 共享，不放進 .llm_io）
  execution_log.md        # 跨 loop 執行紀錄（每次 append）
```

`execution_log.md` 格式（每次 loop append 一行）：

```
| {timestamp} | loop {N} | {strategy_name} | {run_mode} | IS pf={X} wr={Y} | OOS pf={X} wr={Y} | {PASS/FAIL} |
```

---

## 元件職責

### `config_generator.py`

從 `state["spec"]` 讀取欄位生成 `config.json`：

| config 欄位 | spec 來源 |
|-------------|-----------|
| `pair` | `spec["trading_scope"]["pair"]` |
| `timeframe` | `spec["trading_scope"]["timeframe"]` |
| `stake_currency` | 從 pair 自動推導（e.g. USDT） |
| `exchange.name` | `spec["trading_scope"]["exchange"]` |
| `trading_fee` | `spec["execution"]["fee"]` |

其餘必要欄位（`max_open_trades`、`stake_amount` 等）使用固定預設值。

`KeyError`（spec 缺少必要欄位）在此階段 abort，不進入 CLI 呼叫。

---

### `freqtrade_runner.py`

封裝單次回測 subprocess 呼叫：

```python
def run_freqtrade_backtest(
    strategy_name: str,
    strategy_dir: str,
    config_path: str,
    userdir: str,
    timerange: str,      # e.g. "20230101-20231231"
    results_dir: str,
) -> Path:               # 回傳新產生的 .zip 路徑
```

- 呼叫失敗（returncode != 0）時拋出 `RuntimeError`（含 stderr 前 50 行）
- 使用前後 zip 列表差集偵測新產生的 .zip（同 cross_test_runner 做法）
- 支援 retry（最多 3 次，遇 exchange 暫時性錯誤時 backoff）
- 此 module 為 importable library，不含 `if __name__ == "__main__"` 邏輯

---

### `freqtrade_cli.py`

CLI 入口，LLM 在 `implement_node` 中透過 subprocess 呼叫：

```
python projects/quant_alpha/freqtrade_cli.py <subcommand> [options]

subcommand:
  backtest     基本 IS/OOS 回測（plan_output run_mode 預設值）
  hyperopt     參數優化（--epochs N），完成後以最佳參數再跑 backtest
  cross_test   多組參數 grid search + stage2 篩選（--plan path/to/plan.json）
```

每個 subcommand 完成後統一呼叫 `result_parser.py` 生成分析產出。

---

### `result_parser.py`

解析 Freqtrade `.zip`，生成所有分析產出：

| 輸出 | 內容 |
|------|------|
| `loop_N_is.json` / `loop_N_oos.json` | win_rate、profit_factor、max_drawdown\_account、profit\_total\_pct、n\_trades |
| `loop_N_trades.json` | 逐筆交易（進出場時間、價格、損益） |
| `loop_N_signals.json` | entry/exit signal 數據 |
| `loop_N_report.html` | 完整 HTML 報告（指標 + 圖表，參考 analyze\_backtest\_result.py） |

欄位對應：

| 輸出欄位 | Freqtrade JSON 欄位 |
|----------|-------------------|
| `win_rate` | `winrate` |
| `profit_factor` | `profit_factor` |
| `max_drawdown` | `max_drawdown_account` |
| `profit_total_pct` | `profit_total × 100` |
| `n_trades` | `total_trades` |

---

### `plugin.py` 變更

#### Mock Gate

```python
BACKTEST_MODE = os.getenv("BACKTEST_MODE", "mock")

def implement_node(self, state):
    if BACKTEST_MODE == "mock":
        return _mock_implement_result(state)   # deterministic fake，現有 tests 不變
    # ... real flow

def test_node(self, state):
    if BACKTEST_MODE == "mock":
        return _mock_test_result(state)
    # ... real flow
```

Mock 回傳與現有 stub 行為一致（hash seed），所有現有測試零改動。

#### `plan_output.json` 新增欄位

```json
{
  "strategy_name": "RsiMomentumV2",
  "strategy_file": "strategies/RsiMomentumV2.py",
  "run_mode": "backtest",
  "hyperopt_epochs": 50,
  "cross_test_experiments": []
}
```

#### `test_metrics` 格式升級（IS/OOS 雙組）

```python
"test_metrics": {
    "is":  { "win_rate", "profit_factor", "max_drawdown", "n_trades", "profit_total_pct" },
    "oos": { ... 同上 }
}
```

---

### `analyze.txt` 升級

新增 IS/OOS 雙組指標變數，OOS 門檻 = IS 門檻 × 80%（IS 門檻來自 `spec["performance_thresholds"]`）：

- IS PASS 且 OOS PASS → PASS
- IS PASS 但 OOS 落差 > 20% → FAIL（過擬合訊號）
- IS FAIL → FAIL

---

## 錯誤處理

| 情境 | 處理 |
|------|------|
| `freqtrade` 指令不存在 | `FileNotFoundError` → TERMINATE，留言「Freqtrade CLI not found」 |
| returncode != 0 | `RuntimeError`（含 stderr 前 50 行）→ TERMINATE |
| `.zip` 找不到或格式錯誤 | `ValueError` → TERMINATE |
| spec 缺少 pair/timeframe | `KeyError` → config 生成 abort |
| hyperopt 無法取得最佳參數 | fallback 原始參數跑 backtest，log 警告 |

---

## 測試策略

- `BACKTEST_MODE=mock`（預設）：implement\_node / test\_node 回傳 fake 資料，**現有所有 tests 零改動**。
- 新增 `tests/test_freqtrade_integration.py`，標記 `@pytest.mark.freqtrade_real`，預設跳過：
  - `test_config_generator`：驗證 config.json 欄位正確
  - `test_result_parser`：用預存 `.zip` fixture 驗證指標解析
  - `test_freqtrade_cli_backtest`：需真實 Freqtrade 環境

---

## Freqtrade 版本假設

最低支援版本：freqtrade >= 2024.1

欄位名稱（`max_drawdown_account`、`winrate`、`profit_factor`、`total_trades`）以此版本 JSON schema 為準。

---

## 不在此次範疇

- plan / implement 雙 LLM 分工（後續 TODO B 候選）
- Freqtrade 歷史資料下載自動化（需手動執行 T-A03）
- `cross_test` subcommand 的 plan.json schema 定義（plan.json 格式待後續 TODO 補充）
