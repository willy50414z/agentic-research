# Freqtrade Backtest Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `projects/quant_alpha/backtest.py` stub with real Freqtrade CLI calls, controlled by `BACKTEST_MODE` env var, with IS/OOS split and full artifact output per loop.

**Architecture:** `config_generator`, `freqtrade_runner`, `result_parser` are importable Python modules. `freqtrade_cli.py` is the CLI entry point that composes them (called by `implement_node` via subprocess). `backtest.py` becomes the IS/OOS orchestrator. `plugin.py` gates on `BACKTEST_MODE=mock` (default) to preserve all existing tests unchanged.

**Tech Stack:** Python 3.12, subprocess, zipfile, json, argparse, pytest, unittest.mock

---

### Task 1: config_generator.py + 測試框架建立

**Files:**
- Create: `projects/quant_alpha/config_generator.py`
- Create: `tests/test_freqtrade_integration.py`

- [ ] **Step 1: 建立測試框架，寫 config_generator 的失敗測試**

```python
# tests/test_freqtrade_integration.py
"""
Freqtrade 整合測試。
預設使用 unittest.mock，不需要真實 Freqtrade 環境。
@pytest.mark.freqtrade_real 的測試需要真實 Freqtrade CLI 且預設跳過。
"""
from __future__ import annotations

import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_ROOT = str(Path(__file__).parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


# ── Fixtures ──────────────────────────────────────────────────────────────────

SAMPLE_SPEC = {
    "trading_scope": {
        "pair": "BTC/USDT",
        "timeframe": "1h",
        "exchange": "binance",
    },
    "execution": {"fee": "0.10%"},
    "data": {
        "train_period": {"start": "2022-01-01", "end": "2022-12-31"},
        "test_period":  {"start": "2023-01-01", "end": "2023-12-31"},
    },
    "performance_thresholds": {
        "is_win_rate": 0.55,
        "is_profit_factor": 1.2,
    },
}

SAMPLE_PLAN = {
    "strategy_name": "TestRsiStrategy",
    "stoploss": -0.05,
    "run_mode": "backtest",
}


def _make_fixture_zip(strategy_name: str = "TestRsiStrategy") -> bytes:
    """Build a minimal valid Freqtrade backtest .zip in memory."""
    data = {
        "metadata": {"freqtrade_version": "2024.1"},
        "strategy": {
            strategy_name: {
                "winrate": 0.60,
                "profit_factor": 1.50,
                "max_drawdown_account": 0.12,
                "profit_total": 0.25,
                "total_trades": 45,
                "trades": [
                    {
                        "pair": "BTC/USDT",
                        "open_date": "2023-01-01 00:00:00",
                        "close_date": "2023-01-02 00:00:00",
                        "open_rate": 20000.0,
                        "close_rate": 21000.0,
                        "profit_ratio": 0.05,
                    }
                ],
            }
        },
    }
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("backtest-result-2024-01-01_00-00-00.json", json.dumps(data))
    return buf.getvalue()


# ── Task 1: config_generator ──────────────────────────────────────────────────

class TestConfigGenerator:
    def test_generate_config_creates_file(self, tmp_path):
        from projects.quant_alpha.config_generator import generate_config
        path = generate_config(SAMPLE_SPEC, tmp_path)
        assert path.exists()
        assert path.name == "config.json"

    def test_generate_config_fields(self, tmp_path):
        from projects.quant_alpha.config_generator import generate_config
        path = generate_config(SAMPLE_SPEC, tmp_path)
        cfg = json.loads(path.read_text(encoding="utf-8"))
        assert cfg["exchange"]["name"] == "binance"
        assert cfg["exchange"]["pair_whitelist"] == ["BTC/USDT"]
        assert cfg["timeframe"] == "1h"
        assert cfg["stake_currency"] == "USDT"
        assert abs(cfg["fee"] - 0.001) < 1e-9

    def test_generate_config_missing_pair_raises(self, tmp_path):
        from projects.quant_alpha.config_generator import generate_config
        bad_spec = {"trading_scope": {"timeframe": "1h", "exchange": "binance"}, "execution": {"fee": "0.10%"}}
        with pytest.raises(KeyError):
            generate_config(bad_spec, tmp_path)
```

- [ ] **Step 2: 執行測試，確認 FAIL（模組不存在）**

```
pytest tests/test_freqtrade_integration.py::TestConfigGenerator -v
```
Expected: `ModuleNotFoundError` 或 `ImportError`

- [ ] **Step 3: 實作 config_generator.py**

```python
# projects/quant_alpha/config_generator.py
"""
從 spec dict 動態生成 Freqtrade config.json。
Importable library — 無 CLI 入口。
"""
import json
from pathlib import Path


def generate_config(spec: dict, work_dir: Path) -> Path:
    """
    Generate Freqtrade config.json from spec fields and write to work_dir.
    Raises KeyError if required spec fields are missing.
    Returns path to the written config.json.
    """
    scope     = spec["trading_scope"]
    execution = spec.get("execution", {})

    pair      = scope["pair"]
    timeframe = scope["timeframe"]
    exchange  = scope["exchange"]

    # Parse fee: "0.10%" → 0.001
    fee_str = str(execution.get("fee", "0.1%")).rstrip("%")
    fee_pct = float(fee_str) / 100

    # Derive stake_currency from pair: "BTC/USDT" → "USDT"
    stake_currency = pair.split("/")[1] if "/" in pair else "USDT"

    config = {
        "max_open_trades": 1,
        "stake_currency": stake_currency,
        "stake_amount": "unlimited",
        "tradable_balance_ratio": 1.0,
        "fiat_display_currency": "USD",
        "timeframe": timeframe,
        "dry_run": True,
        "dry_run_wallet": 1000,
        "trading_mode": "spot",
        "margin_mode": "",
        "exchange": {
            "name": exchange,
            "key": "",
            "secret": "",
            "ccxt_config": {},
            "ccxt_async_config": {},
            "pair_whitelist": [pair],
        },
        "fee": fee_pct,
        "internals": {},
    }

    work_dir.mkdir(parents=True, exist_ok=True)
    config_path = work_dir / "config.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    return config_path
```

- [ ] **Step 4: 執行測試，確認 PASS**

```
pytest tests/test_freqtrade_integration.py::TestConfigGenerator -v
```
Expected: 3 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add projects/quant_alpha/config_generator.py tests/test_freqtrade_integration.py
git commit -m "feat: add config_generator and integration test scaffold"
```

---

### Task 2: freqtrade_runner.py

**Files:**
- Create: `projects/quant_alpha/freqtrade_runner.py`
- Modify: `tests/test_freqtrade_integration.py`

- [ ] **Step 1: 新增 freqtrade_runner 測試**

在 `tests/test_freqtrade_integration.py` 的 `Task 1` 測試後面加入：

```python
# ── Task 2: freqtrade_runner ──────────────────────────────────────────────────

class TestFreqtradeRunner:
    def test_success_returns_zip_path(self, tmp_path):
        """Mock subprocess success — returns newest .zip in results_dir."""
        from projects.quant_alpha.freqtrade_runner import run_freqtrade_backtest
        results_dir = tmp_path / "backtest_results"
        results_dir.mkdir()
        zip_path = results_dir / "backtest-result-2024-01-01_00-00-00.zip"
        zip_path.write_bytes(b"PK")  # dummy zip

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = run_freqtrade_backtest(
                strategy_name="TestStrategy",
                strategy_dir=str(tmp_path / "strategies"),
                config_path=str(tmp_path / "config.json"),
                userdir=str(tmp_path / "user_data"),
                timerange="20230101-20231231",
                results_dir=str(results_dir),
            )
        assert result == zip_path

    def test_cli_not_found_raises(self, tmp_path):
        from projects.quant_alpha.freqtrade_runner import run_freqtrade_backtest
        results_dir = tmp_path / "backtest_results"
        results_dir.mkdir()
        with patch("subprocess.run", side_effect=FileNotFoundError):
            with pytest.raises(FileNotFoundError, match="freqtrade CLI not found"):
                run_freqtrade_backtest(
                    strategy_name="S", strategy_dir=".", config_path="c.json",
                    userdir=".", timerange="20230101-20231231",
                    results_dir=str(results_dir),
                )

    def test_nonzero_exit_raises_runtime_error(self, tmp_path):
        from projects.quant_alpha.freqtrade_runner import run_freqtrade_backtest
        results_dir = tmp_path / "backtest_results"
        results_dir.mkdir()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stdout="", stderr="Error line 1\nError line 2"
            )
            with pytest.raises(RuntimeError, match="exited with code 1"):
                run_freqtrade_backtest(
                    strategy_name="S", strategy_dir=".", config_path="c.json",
                    userdir=".", timerange="20230101-20231231",
                    results_dir=str(results_dir),
                    max_retries=1,
                )

    def test_no_new_zip_raises_value_error(self, tmp_path):
        from projects.quant_alpha.freqtrade_runner import run_freqtrade_backtest
        results_dir = tmp_path / "backtest_results"
        results_dir.mkdir()
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            with pytest.raises(ValueError, match="no new .zip"):
                run_freqtrade_backtest(
                    strategy_name="S", strategy_dir=".", config_path="c.json",
                    userdir=".", timerange="20230101-20231231",
                    results_dir=str(results_dir),
                )
```

- [ ] **Step 2: 執行測試，確認 FAIL**

```
pytest tests/test_freqtrade_integration.py::TestFreqtradeRunner -v
```
Expected: ImportError

- [ ] **Step 3: 實作 freqtrade_runner.py**

```python
# projects/quant_alpha/freqtrade_runner.py
"""
Freqtrade CLI subprocess wrapper。
Importable library — 無 CLI 入口。
backtest.py 和 freqtrade_cli.py 都直接 import 此模組。
"""
import subprocess
import time
from pathlib import Path


def run_freqtrade_backtest(
    strategy_name: str,
    strategy_dir: str,
    config_path: str,
    userdir: str,
    timerange: str,
    results_dir: str,
    max_retries: int = 3,
) -> Path:
    """
    Call freqtrade backtesting CLI for a single timerange.
    Returns Path to the newly created .zip result file.
    Raises FileNotFoundError if freqtrade CLI is not installed.
    Raises RuntimeError on non-zero exit after max_retries.
    Raises ValueError if no new .zip is detected after success.
    """
    Path(results_dir).mkdir(parents=True, exist_ok=True)

    def _list_zips() -> set[Path]:
        return set(Path(results_dir).glob("*.zip"))

    before = _list_zips()

    cmd = [
        "freqtrade", "backtesting",
        "--config", config_path,
        "--strategy", strategy_name,
        "--strategy-path", strategy_dir,
        "--userdir", userdir,
        "--timerange", timerange,
        "--export", "trades",
        "--cache", "none",
    ]

    for attempt in range(1, max_retries + 1):
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except FileNotFoundError:
            raise FileNotFoundError(
                "freqtrade CLI not found. Install with: pip install freqtrade"
            )

        if proc.returncode == 0:
            break

        stderr_preview = "\n".join((proc.stderr or "").splitlines()[:50])
        if attempt == max_retries:
            raise RuntimeError(
                f"freqtrade exited with code {proc.returncode} "
                f"(attempt {attempt}/{max_retries}):\n{stderr_preview}"
            )
        time.sleep(2 ** attempt)

    after = _list_zips()
    new_zips = after - before
    if not new_zips:
        raise ValueError(
            f"freqtrade completed but no new .zip found in {results_dir}"
        )
    return max(new_zips, key=lambda p: p.stat().st_mtime)
```

- [ ] **Step 4: 執行測試，確認 PASS**

```
pytest tests/test_freqtrade_integration.py::TestFreqtradeRunner -v
```
Expected: 4 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add projects/quant_alpha/freqtrade_runner.py tests/test_freqtrade_integration.py
git commit -m "feat: add freqtrade_runner subprocess wrapper"
```

---

### Task 3: result_parser.py

**Files:**
- Create: `projects/quant_alpha/result_parser.py`
- Modify: `tests/test_freqtrade_integration.py`

- [ ] **Step 1: 新增 result_parser 測試**

```python
# ── Task 3: result_parser ─────────────────────────────────────────────────────

class TestResultParser:
    def test_parse_backtest_zip_basic(self, tmp_path):
        from projects.quant_alpha.result_parser import parse_backtest_zip
        zip_path = tmp_path / "backtest-result-2024.zip"
        zip_path.write_bytes(_make_fixture_zip("TestRsiStrategy"))
        metrics = parse_backtest_zip(zip_path, "TestRsiStrategy")
        assert metrics["win_rate"] == pytest.approx(0.60)
        assert metrics["profit_factor"] == pytest.approx(1.50)
        assert metrics["max_drawdown"] == pytest.approx(0.12)
        assert metrics["profit_total_pct"] == pytest.approx(25.0)
        assert metrics["n_trades"] == 45
        assert isinstance(metrics["trades"], list)

    def test_parse_backtest_zip_wrong_strategy(self, tmp_path):
        from projects.quant_alpha.result_parser import parse_backtest_zip
        zip_path = tmp_path / "backtest-result-2024.zip"
        zip_path.write_bytes(_make_fixture_zip("TestRsiStrategy"))
        with pytest.raises(ValueError, match="not found"):
            parse_backtest_zip(zip_path, "WrongStrategy")

    def test_parse_backtest_zip_bad_zip(self, tmp_path):
        from projects.quant_alpha.result_parser import parse_backtest_zip
        bad_zip = tmp_path / "bad.zip"
        bad_zip.write_bytes(b"not a zip")
        with pytest.raises(ValueError, match="Invalid zip"):
            parse_backtest_zip(bad_zip, "Any")

    def test_write_loop_artifacts_creates_all_files(self, tmp_path):
        from projects.quant_alpha.result_parser import write_loop_artifacts
        is_m  = {"win_rate": 0.6, "profit_factor": 1.5, "max_drawdown": 0.12,
                  "profit_total_pct": 25.0, "n_trades": 45, "trades": []}
        oos_m = {"win_rate": 0.55, "profit_factor": 1.3, "max_drawdown": 0.14,
                  "profit_total_pct": 20.0, "n_trades": 38, "trades": [
                      {"pair": "BTC/USDT", "open_date": "2023-06-01 00:00:00",
                       "close_date": "2023-06-02 00:00:00",
                       "open_rate": 25000.0, "close_rate": 26000.0, "profit_ratio": 0.04}
                  ]}
        write_loop_artifacts(is_m, oos_m, tmp_path, loop=0)
        assert (tmp_path / "loop_0_is.json").exists()
        assert (tmp_path / "loop_0_oos.json").exists()
        assert (tmp_path / "loop_0_trades.json").exists()
        assert (tmp_path / "loop_0_signals.json").exists()
        assert (tmp_path / "loop_0_report.html").exists()
        is_data = json.loads((tmp_path / "loop_0_is.json").read_text())
        assert is_data["win_rate"] == pytest.approx(0.6)
        # Ensure trades list not included in metrics files
        assert "trades" not in is_data
```

- [ ] **Step 2: 執行測試，確認 FAIL**

```
pytest tests/test_freqtrade_integration.py::TestResultParser -v
```
Expected: ImportError

- [ ] **Step 3: 實作 result_parser.py**

```python
# projects/quant_alpha/result_parser.py
"""
解析 Freqtrade backtest .zip，生成指標 JSON、交易記錄、訊號 JSON、HTML 報告。
Importable library — 無 CLI 入口。
"""
import json
import zipfile
from pathlib import Path


def parse_backtest_zip(zip_path: Path, strategy_name: str) -> dict:
    """
    Extract metrics from a Freqtrade backtest .zip.
    Returns dict with: win_rate, profit_factor, max_drawdown,
                       profit_total_pct, n_trades, trades (list).
    Raises ValueError on bad zip or missing strategy.
    """
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            json_names = [
                n for n in zf.namelist()
                if n.endswith(".json")
                and not n.endswith(".meta.json")
                and not n.endswith("_config.json")
            ]
            if not json_names:
                raise ValueError(f"No main JSON in {zip_path}")
            raw = json.loads(zf.read(json_names[0]).decode("utf-8", errors="replace"))
    except zipfile.BadZipFile as e:
        raise ValueError(f"Invalid zip: {zip_path}: {e}") from e

    strategy_data = raw.get("strategy", {}).get(strategy_name)
    if strategy_data is None:
        available = list(raw.get("strategy", {}).keys())
        raise ValueError(
            f"Strategy '{strategy_name}' not found in {zip_path}. "
            f"Available: {available}"
        )

    return {
        "win_rate":          round(float(strategy_data.get("winrate", 0)), 6),
        "profit_factor":     round(float(strategy_data.get("profit_factor", 0)), 6),
        "max_drawdown":      round(float(strategy_data.get("max_drawdown_account", 0)), 6),
        "profit_total_pct":  round(float(strategy_data.get("profit_total", 0)) * 100, 4),
        "n_trades":          int(strategy_data.get("total_trades", 0)),
        "trades":            list(strategy_data.get("trades", [])),
    }


def write_loop_artifacts(
    is_metrics: dict,
    oos_metrics: dict,
    work_dir: Path,
    loop: int,
) -> None:
    """
    Write all loop artifacts to work_dir:
      loop_{N}_is.json, loop_{N}_oos.json,
      loop_{N}_trades.json, loop_{N}_signals.json, loop_{N}_report.html
    """
    work_dir.mkdir(parents=True, exist_ok=True)

    is_clean  = {k: v for k, v in is_metrics.items()  if k != "trades"}
    oos_clean = {k: v for k, v in oos_metrics.items() if k != "trades"}

    (work_dir / f"loop_{loop}_is.json").write_text(
        json.dumps(is_clean, indent=2), encoding="utf-8")
    (work_dir / f"loop_{loop}_oos.json").write_text(
        json.dumps(oos_clean, indent=2), encoding="utf-8")

    trades = oos_metrics.get("trades", [])
    (work_dir / f"loop_{loop}_trades.json").write_text(
        json.dumps(trades, indent=2), encoding="utf-8")

    signals = [
        {
            "pair":         t.get("pair"),
            "enter_date":   t.get("open_date"),
            "exit_date":    t.get("close_date"),
            "enter_rate":   t.get("open_rate"),
            "exit_rate":    t.get("close_rate"),
            "profit_ratio": t.get("profit_ratio"),
        }
        for t in trades
    ]
    (work_dir / f"loop_{loop}_signals.json").write_text(
        json.dumps(signals, indent=2), encoding="utf-8")

    html = _build_html_report(loop, is_clean, oos_clean, trades)
    (work_dir / f"loop_{loop}_report.html").write_text(html, encoding="utf-8")


def _build_html_report(loop: int, is_m: dict, oos_m: dict, trades: list) -> str:
    def _row(k: str, v) -> str:
        return f"<tr><td>{k}</td><td>{v}</td></tr>"

    metric_rows = "\n".join([
        _row("win_rate (IS)",        is_m.get("win_rate", 0)),
        _row("win_rate (OOS)",       oos_m.get("win_rate", 0)),
        _row("profit_factor (IS)",   is_m.get("profit_factor", 0)),
        _row("profit_factor (OOS)",  oos_m.get("profit_factor", 0)),
        _row("max_drawdown (IS)",    is_m.get("max_drawdown", 0)),
        _row("max_drawdown (OOS)",   oos_m.get("max_drawdown", 0)),
        _row("n_trades (IS)",        is_m.get("n_trades", 0)),
        _row("n_trades (OOS)",       oos_m.get("n_trades", 0)),
    ])
    trade_rows = "\n".join([
        f"<tr><td>{t.get('pair','')}</td><td>{t.get('open_date','')}</td>"
        f"<td>{t.get('close_date','')}</td>"
        f"<td>{t.get('profit_ratio', 0):.4f}</td></tr>"
        for t in trades[:100]
    ])

    return (
        f"<!DOCTYPE html><html><head><title>Loop {loop} Backtest Report</title>"
        f"<style>table{{border-collapse:collapse}}td,th{{border:1px solid #ccc;padding:4px 8px}}"
        f"</style></head><body>"
        f"<h1>Loop {loop} Backtest Report</h1>"
        f"<h2>Metrics</h2>"
        f"<table><tr><th>Metric</th><th>Value</th></tr>{metric_rows}</table>"
        f"<h2>OOS Trades (first 100)</h2>"
        f"<table><tr><th>Pair</th><th>Open</th><th>Close</th><th>Profit</th></tr>"
        f"{trade_rows}</table></body></html>"
    )
```

- [ ] **Step 4: 執行測試，確認 PASS**

```
pytest tests/test_freqtrade_integration.py::TestResultParser -v
```
Expected: 4 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add projects/quant_alpha/result_parser.py tests/test_freqtrade_integration.py
git commit -m "feat: add result_parser for Freqtrade zip parsing and artifact writing"
```

---

### Task 4: backtest.py 改寫（IS/OOS 編排）

**Files:**
- Modify: `projects/quant_alpha/backtest.py`
- Modify: `tests/test_freqtrade_integration.py`

- [ ] **Step 1: 新增 backtest real mode 測試**

```python
# ── Task 4: backtest real mode ────────────────────────────────────────────────

class TestBacktestRealMode:
    def test_run_backtest_is_oos_calls_runner_twice(self, tmp_path):
        """run_backtest_is_oos should call run_freqtrade_backtest twice (IS, OOS)."""
        from projects.quant_alpha import backtest as bt

        fake_zip = tmp_path / "fake.zip"
        fake_zip.write_bytes(_make_fixture_zip("TestRsiStrategy"))

        with patch("projects.quant_alpha.backtest.run_freqtrade_backtest",
                   return_value=fake_zip) as mock_runner, \
             patch("projects.quant_alpha.backtest.generate_config",
                   return_value=tmp_path / "config.json"):
            is_m, oos_m = bt.run_backtest_is_oos(
                spec=SAMPLE_SPEC,
                plan=SAMPLE_PLAN,
                work_dir=tmp_path,
                userdir=tmp_path / "user_data",
            )

        assert mock_runner.call_count == 2
        calls = mock_runner.call_args_list
        assert calls[0].kwargs["timerange"] == "20220101-20221231"
        assert calls[1].kwargs["timerange"] == "20230101-20231231"
        assert is_m["win_rate"] == pytest.approx(0.60)
        assert oos_m["win_rate"] == pytest.approx(0.60)

    def test_to_freqtrade_timerange(self):
        from projects.quant_alpha.backtest import _to_freqtrade_timerange
        period = {"start": "2023-01-01", "end": "2023-12-31"}
        assert _to_freqtrade_timerange(period) == "20230101-20231231"
```

- [ ] **Step 2: 執行測試，確認 FAIL**

```
pytest tests/test_freqtrade_integration.py::TestBacktestRealMode -v
```
Expected: ImportError 或 AttributeError

- [ ] **Step 3: 改寫 backtest.py（移除 stub，加入 IS/OOS 編排）**

```python
# projects/quant_alpha/backtest.py
"""
projects/quant_alpha/backtest.py

Real Freqtrade backtest IS/OOS orchestrator.
Calls config_generator, freqtrade_runner, result_parser.

Usage:
    from projects.quant_alpha.backtest import run_backtest_is_oos

    is_metrics, oos_metrics = run_backtest_is_oos(spec, plan, work_dir, userdir)
    # Each metrics dict: {win_rate, profit_factor, max_drawdown,
    #                     profit_total_pct, n_trades, trades}
"""
from pathlib import Path
from typing import Any

from projects.quant_alpha.config_generator import generate_config
from projects.quant_alpha.freqtrade_runner import run_freqtrade_backtest
from projects.quant_alpha.result_parser import parse_backtest_zip


def run_backtest_is_oos(
    spec: dict[str, Any],
    plan: dict[str, Any],
    work_dir: Path,
    userdir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Run IS and OOS backtests via Freqtrade CLI.
    Returns (is_metrics, oos_metrics).

    IS timerange  ← spec["data"]["train_period"]
    OOS timerange ← spec["data"]["test_period"]
    """
    strategy_name = plan["strategy_name"]
    strategy_dir  = str(work_dir / "strategies")
    results_dir   = str(work_dir / "backtest_results")

    config_path = generate_config(spec, work_dir)

    is_range  = _to_freqtrade_timerange(spec["data"]["train_period"])
    oos_range = _to_freqtrade_timerange(spec["data"]["test_period"])

    is_zip = run_freqtrade_backtest(
        strategy_name=strategy_name,
        strategy_dir=strategy_dir,
        config_path=str(config_path),
        userdir=str(userdir),
        timerange=is_range,
        results_dir=results_dir,
    )
    oos_zip = run_freqtrade_backtest(
        strategy_name=strategy_name,
        strategy_dir=strategy_dir,
        config_path=str(config_path),
        userdir=str(userdir),
        timerange=oos_range,
        results_dir=results_dir,
    )

    is_metrics  = parse_backtest_zip(is_zip,  strategy_name)
    oos_metrics = parse_backtest_zip(oos_zip, strategy_name)
    return is_metrics, oos_metrics


def _to_freqtrade_timerange(period: dict[str, str]) -> str:
    """{"start": "2023-01-01", "end": "2023-12-31"} → "20230101-20231231" """
    start = str(period["start"]).replace("-", "")
    end   = str(period["end"]).replace("-", "")
    return f"{start}-{end}"
```

- [ ] **Step 4: 執行測試，確認 PASS**

```
pytest tests/test_freqtrade_integration.py::TestBacktestRealMode -v
```
Expected: 2 tests PASSED

- [ ] **Step 5: Commit**

```bash
git add projects/quant_alpha/backtest.py tests/test_freqtrade_integration.py
git commit -m "feat: replace backtest stub with real IS/OOS orchestrator"
```

---

### Task 5: plugin.py — BACKTEST_MODE mock gate

**Files:**
- Modify: `projects/quant_alpha/plugin.py`

此任務只做 mock gate：不改變 implement_node / test_node 的外部行為，將現有邏輯包入 `_mock_*` 函式，加入 `BACKTEST_MODE` 檢查。所有現有測試應繼續 PASS。

- [ ] **Step 1: 先確認現有測試基線**

```
pytest tests/ -v -m "not integration" 2>&1 | tail -20
```
Expected: 全部 PASSED（記下通過數量）

- [ ] **Step 2: 在 plugin.py 開頭加入 BACKTEST_MODE 與 mock helpers**

在 `plugin.py` 的 `ARTIFACTS_DIR = ...` 行後面，加入：

```python
BACKTEST_MODE = os.getenv("BACKTEST_MODE", "mock")


# ---------------------------------------------------------------------------
# Mock helpers (BACKTEST_MODE=mock, default)
# ---------------------------------------------------------------------------

def _mock_implement_result(state: dict) -> dict:
    """Deterministic fake implement result — same hash seed as former stub."""
    import hashlib
    import random as _random

    loop   = state.get("loop_index", 0)
    plan   = state.get("implementation_plan", {}) or {}

    seed_input = f"700{plan.get('strategy_name', '')}{sorted(plan.items())}"
    seed = int(hashlib.md5(seed_input.encode()).hexdigest(), 16) % 100_000
    rng  = _random.Random(seed)

    n_trades     = rng.randint(20, 80)
    win_rate     = round(rng.uniform(0.45, 0.75), 4)
    total_return = round(rng.uniform(-0.10, 0.40), 4)
    alpha_ratio  = round(rng.uniform(0.7, 2.5), 4)
    max_drawdown = round(rng.uniform(0.05, 0.30), 4)
    gross_profit = round(rng.uniform(0.1, 0.5), 4)
    gross_loss   = round(rng.uniform(0.05, 0.4), 4)
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 1e-9 else 9.99

    is_result = {
        "win_rate":          win_rate,
        "alpha_ratio":       alpha_ratio,
        "max_drawdown":      max_drawdown,
        "n_trades":          n_trades,
        "total_return":      total_return,
        "profit_total_pct":  round(total_return * 100, 4),
        "profit_factor":     profit_factor,
    }

    artifact_path = str(ARTIFACTS_DIR / f"loop_{loop}_train.json")
    _write_artifact(artifact_path, json.dumps(
        {"loop": loop, "plan": plan, "is_result": is_result}, indent=2))

    return {
        "needs_human_approval": False,
        "is_metrics": is_result,
        "artifacts": state.get("artifacts", []) + [
            {"type": "train_result", "path": artifact_path}
        ],
    }


def _mock_test_result(state: dict) -> dict:
    """Deterministic fake test result — same hash seed as former stub."""
    import hashlib
    import random as _random

    plan    = state.get("implementation_plan", {}) or {}
    attempt = state.get("attempt_count", 0) + 1
    n_bars  = 300 + attempt * 50

    seed_input = f"{n_bars}{plan.get('strategy_name', '')}{sorted(plan.items())}"
    seed = int(hashlib.md5(seed_input.encode()).hexdigest(), 16) % 100_000
    rng  = _random.Random(seed)

    n_trades     = rng.randint(20, 80)
    win_rate     = round(rng.uniform(0.45, 0.75), 4)
    total_return = round(rng.uniform(-0.10, 0.40), 4)
    alpha_ratio  = round(rng.uniform(0.7, 2.5), 4)
    max_drawdown = round(rng.uniform(0.05, 0.30), 4)
    gross_profit = round(rng.uniform(0.1, 0.5), 4)
    gross_loss   = round(rng.uniform(0.05, 0.4), 4)
    profit_factor = round(gross_profit / gross_loss, 4) if gross_loss > 1e-9 else 9.99

    return {
        "attempt_count": attempt,
        "test_metrics": {
            "win_rate":      win_rate,
            "alpha_ratio":   alpha_ratio,
            "max_drawdown":  max_drawdown,
            "n_trades":      n_trades,
            "total_return":  total_return,
            "profit_factor": profit_factor,
        },
    }
```

- [ ] **Step 3: 改寫 implement_node — 加入 mock gate**

將 `implement_node` 中 `if state.get("needs_human_approval")` 判斷結束後的 backtest 呼叫部分，替換為：

```python
    def implement_node(self, state: dict) -> dict:
        loop = state.get("loop_index", 0)
        plan = state.get("implementation_plan", {})
        logger.info("[QuantAlpha] implement  loop=%d  strategy=%s",
                    loop, plan.get("strategy_type"))

        if state.get("needs_human_approval", False):
            logger.info("[QuantAlpha] implement  ⏸ waiting for plan review")
            decision = interrupt({
                "checkpoint": "plan_review",
                "loop_index": loop,
                "plan":       plan,
                "instruction": "Resume: {'action': 'approve'} or {'action': 'reject', 'reason': '...'}",
            })
            if isinstance(decision, dict) and decision.get("action") == "reject":
                reason = decision.get("reason", "Plan rejected.")
                logger.info("[QuantAlpha] implement  plan rejected: %s", reason)
                return {"last_result": "TERMINATE", "last_reason": reason,
                        "needs_human_approval": False}

        if BACKTEST_MODE == "mock":
            return _mock_implement_result(state)

        # ── Real mode ────────────────────────────────────────────────────────
        return self._real_implement(state)

    def _real_implement(self, state: dict) -> dict:
        """Real Freqtrade IS/OOS backtest. Called only when BACKTEST_MODE != 'mock'."""
        # Placeholder — filled in Task 6
        raise NotImplementedError("Real implement_node not yet implemented")
```

- [ ] **Step 4: 改寫 test_node — 加入 mock gate**

```python
    def test_node(self, state: dict) -> dict:
        loop    = state.get("loop_index", 0)
        attempt = state.get("attempt_count", 0) + 1
        plan    = state.get("implementation_plan", {})
        logger.info("[QuantAlpha] test  loop=%d  attempt=%d  strategy=%s",
                    loop, attempt, plan.get("strategy_type"))

        if BACKTEST_MODE == "mock":
            return _mock_test_result(state)

        # ── Real mode ────────────────────────────────────────────────────────
        oos = state.get("oos_metrics", {})
        logger.info("[QuantAlpha] test  win_rate=%.4f  drawdown=%.4f  pf=%.4f",
                    oos.get("win_rate", 0), oos.get("max_drawdown", 0), oos.get("profit_factor", 0))
        return {
            "attempt_count": attempt,
            "test_metrics": {
                "is":  state.get("is_metrics", {}),
                "oos": oos,
            },
        }
```

- [ ] **Step 5: 確認所有既有測試仍然 PASS**

```
pytest tests/ -v -m "not integration" 2>&1 | tail -20
```
Expected: 與 Step 1 相同的通過數量，全部 PASSED

- [ ] **Step 6: Commit**

```bash
git add projects/quant_alpha/plugin.py
git commit -m "feat: add BACKTEST_MODE mock gate to implement_node and test_node"
```

---

### Task 6: plugin.py — real implement_node（IS/OOS + execution_log）

**Files:**
- Modify: `projects/quant_alpha/plugin.py`
- Modify: `tests/test_freqtrade_integration.py`

- [ ] **Step 1: 新增 real implement_node 測試**

```python
# ── Task 6: real implement_node ───────────────────────────────────────────────

class TestRealImplementNode:
    def test_real_implement_writes_artifacts(self, tmp_path, monkeypatch):
        """Real implement_node: runs IS/OOS, writes artifacts, appends execution_log."""
        import os
        monkeypatch.setenv("BACKTEST_MODE", "real")
        monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))

        # Reimport plugin with new env
        import importlib
        import projects.quant_alpha.plugin as plugin_mod
        importlib.reload(plugin_mod)

        fake_zip = tmp_path / "fake.zip"
        fake_zip.write_bytes(_make_fixture_zip("TestRsiStrategy"))

        with patch("projects.quant_alpha.plugin.run_backtest_is_oos") as mock_bt, \
             patch("projects.quant_alpha.plugin.write_loop_artifacts"):
            mock_bt.return_value = (
                {"win_rate": 0.6, "profit_factor": 1.5, "max_drawdown": 0.12,
                 "profit_total_pct": 25.0, "n_trades": 45, "trades": []},
                {"win_rate": 0.55, "profit_factor": 1.3, "max_drawdown": 0.14,
                 "profit_total_pct": 20.0, "n_trades": 38, "trades": []},
            )
            plugin = plugin_mod.QuantAlphaPlugin()
            state = {
                "loop_index": 0,
                "implementation_plan": SAMPLE_PLAN,
                "spec": SAMPLE_SPEC,
                "artifacts": [],
                "needs_human_approval": False,
            }
            result = plugin.implement_node(state)

        assert result["is_metrics"]["win_rate"] == pytest.approx(0.6)
        assert result["oos_metrics"]["win_rate"] == pytest.approx(0.55)
        assert mock_bt.call_count == 1
```

- [ ] **Step 2: 執行測試，確認 FAIL（NotImplementedError）**

```
pytest tests/test_freqtrade_integration.py::TestRealImplementNode -v
```
Expected: FAIL with NotImplementedError

- [ ] **Step 3: 實作 _real_implement**

在 `plugin.py` 頂部的 import 區塊加入（在既有 import 後）：

```python
from projects.quant_alpha.backtest import run_backtest_is_oos
from projects.quant_alpha.result_parser import write_loop_artifacts
```

將 `_real_implement` 替換為：

```python
    def _real_implement(self, state: dict) -> dict:
        """Real Freqtrade IS/OOS backtest."""
        from datetime import datetime

        loop = state.get("loop_index", 0)
        plan = state.get("implementation_plan", {}) or {}
        spec = state.get("spec") or {}

        timestamp  = datetime.now().strftime("%Y%m%d_%H%M%S")
        work_dir   = ARTIFACTS_DIR / ".llm_io" / f"{loop}_{timestamp}"
        userdir    = ARTIFACTS_DIR / "user_data"
        work_dir.mkdir(parents=True, exist_ok=True)
        userdir.mkdir(parents=True, exist_ok=True)

        logger.info("[QuantAlpha] real implement  loop=%d  work_dir=%s", loop, work_dir)

        is_metrics, oos_metrics = run_backtest_is_oos(
            spec=spec,
            plan=plan,
            work_dir=work_dir,
            userdir=userdir,
        )

        write_loop_artifacts(is_metrics, oos_metrics, work_dir, loop=loop)
        _append_execution_log(loop, plan, is_metrics, oos_metrics)

        logger.info(
            "[QuantAlpha] real implement  IS win_rate=%.4f pf=%.4f | OOS win_rate=%.4f pf=%.4f",
            is_metrics.get("win_rate", 0),  is_metrics.get("profit_factor", 0),
            oos_metrics.get("win_rate", 0), oos_metrics.get("profit_factor", 0),
        )

        artifact_path = str(work_dir / f"loop_{loop}_is.json")
        return {
            "needs_human_approval": False,
            "is_metrics":           is_metrics,
            "oos_metrics":          oos_metrics,
            "artifacts": state.get("artifacts", []) + [
                {"type": "is_result",  "path": artifact_path},
                {"type": "oos_result", "path": str(work_dir / f"loop_{loop}_oos.json")},
            ],
        }
```

新增 `_append_execution_log` helper（在 `_write_artifact` 函式後面）：

```python
def _append_execution_log(
    loop: int,
    plan: dict,
    is_metrics: dict,
    oos_metrics: dict,
    result: str = "—",
) -> None:
    """Append one line to artifacts/execution_log.md."""
    from datetime import datetime

    log_path = ARTIFACTS_DIR / "execution_log.md"
    ts           = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    strategy     = plan.get("strategy_name", "?")
    run_mode     = plan.get("run_mode", "backtest")
    is_pf        = is_metrics.get("profit_factor", 0)
    is_wr        = is_metrics.get("win_rate", 0)
    oos_pf       = oos_metrics.get("profit_factor", 0)
    oos_wr       = oos_metrics.get("win_rate", 0)

    line = (
        f"| {ts} | loop {loop} | {strategy} | {run_mode} "
        f"| IS pf={is_pf:.3f} wr={is_wr:.3f} "
        f"| OOS pf={oos_pf:.3f} wr={oos_wr:.3f} "
        f"| {result} |\n"
    )

    try:
        if not log_path.exists():
            log_path.write_text(
                "| timestamp | loop | strategy | mode | IS | OOS | result |\n"
                "|-----------|------|----------|------|-----|-----|--------|\n",
                encoding="utf-8",
            )
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line)
    except OSError as e:
        logger.warning("[QuantAlpha] execution_log write failed: %s", e)
```

- [ ] **Step 4: 執行測試，確認 PASS**

```
pytest tests/test_freqtrade_integration.py::TestRealImplementNode -v
```
Expected: 1 test PASSED

- [ ] **Step 5: 確認 mock mode 測試仍然 PASS**

```
pytest tests/ -v -m "not integration" 2>&1 | tail -20
```
Expected: 全部 PASSED

- [ ] **Step 6: Commit**

```bash
git add projects/quant_alpha/plugin.py tests/test_freqtrade_integration.py
git commit -m "feat: implement real_implement_node with IS/OOS backtest and execution_log"
```

---

### Task 7: plugin.py — analyze_node 升級支援 IS/OOS 格式

**Files:**
- Modify: `projects/quant_alpha/plugin.py`

- [ ] **Step 1: 更新 analyze_node 支援 IS/OOS test_metrics 格式**

找到 `analyze_node` 中以下片段：

```python
        metrics = state.get("test_metrics", {})
```

改為：

```python
        raw_metrics = state.get("test_metrics", {})
        # Support IS/OOS nested format (real mode) and flat format (mock mode)
        is_metrics  = raw_metrics.get("is",  raw_metrics)
        oos_metrics = raw_metrics.get("oos", raw_metrics)
        metrics = oos_metrics   # analyze against OOS (primary evaluation)
```

更新 prompt 呼叫，加入 IS/OOS 變數（找到 `prompt = _load_prompt("analyze").format(` 片段）：

```python
        prompt = _load_prompt("analyze").format(
            strategy_name        = plan.get("strategy_name", "?"),
            params               = json.dumps({k: v for k, v in plan.items()
                                               if k != "target_win_rate"}),
            is_win_rate          = is_metrics.get("win_rate",      0),
            is_profit_factor     = is_metrics.get("profit_factor", 0),
            is_max_drawdown      = is_metrics.get("max_drawdown",  0),
            is_n_trades          = is_metrics.get("n_trades",      0),
            win_rate             = metrics.get("win_rate",         0),
            alpha_ratio          = metrics.get("alpha_ratio",      0),
            max_drawdown         = metrics.get("max_drawdown",     0),
            profit_factor        = metrics.get("profit_factor",    0.0),
            n_trades             = metrics.get("n_trades",         0),
            target_win_rate      = plan.get("target_win_rate", _PASS_WIN_RATE),
            target_profit_factor = target_pf,
            loop_index           = loop,
            RULES_PATH           = _RULES_PATH,
            OUTPUT_DIR           = output_dir,
        )
```

- [ ] **Step 2: 更新 _rule_based_analyze 支援 IS/OOS**

找到 `_rule_based_analyze` 並更新：

```python
    def _rule_based_analyze(self, loop, plan, metrics):
        # Support IS/OOS nested format or flat format
        if "oos" in metrics:
            oos = metrics["oos"]
            is_m = metrics.get("is", {})
            # Check overfitting: OOS profit_factor < IS * 0.8
            is_pf   = is_m.get("profit_factor", 0)
            oos_pf  = oos.get("profit_factor", 0)
            is_wr   = is_m.get("win_rate", 0)
            oos_wr  = oos.get("win_rate", 0)
            if is_pf > 0 and oos_pf < is_pf * 0.8:
                return "FAIL", (
                    f"Overfitting: OOS pf={oos_pf:.4f} < IS pf={is_pf:.4f} × 0.8"
                )
            if is_wr > 0 and oos_wr < is_wr * 0.8:
                return "FAIL", (
                    f"Overfitting: OOS wr={oos_wr:.4f} < IS wr={is_wr:.4f} × 0.8"
                )
            m = oos
        else:
            m = metrics

        win_rate      = m.get("win_rate", 0)
        alpha_ratio   = m.get("alpha_ratio")   # None in real mode (not computed)
        max_dd        = m.get("max_drawdown", 1)
        profit_factor = m.get("profit_factor", 0.0)
        target_wr     = plan.get("target_win_rate", _PASS_WIN_RATE)

        alpha_ok = (alpha_ratio is None) or (alpha_ratio >= _PASS_ALPHA)

        if (win_rate >= target_wr and alpha_ok
                and max_dd <= _PASS_MAX_DD and profit_factor >= _PASS_PROFIT_FACTOR):
            return "PASS", (
                f"win_rate={win_rate:.4f} ≥ {target_wr}  "
                f"drawdown={max_dd:.4f} ≤ 0.20  "
                f"profit_factor={profit_factor:.4f} ≥ {_PASS_PROFIT_FACTOR}"
            )

        fails = []
        if win_rate      < target_wr:           fails.append(f"win_rate={win_rate:.4f} < {target_wr}")
        if not alpha_ok:                         fails.append(f"alpha={alpha_ratio:.4f} < 1.0")
        if max_dd        > _PASS_MAX_DD:        fails.append(f"drawdown={max_dd:.4f} > 0.20")
        if profit_factor < _PASS_PROFIT_FACTOR: fails.append(f"profit_factor={profit_factor:.4f} < {_PASS_PROFIT_FACTOR}")
        return "FAIL", "Failed: " + "; ".join(fails)
```

- [ ] **Step 3: 確認所有既有測試仍然 PASS**

```
pytest tests/ -v -m "not integration" 2>&1 | tail -20
```
Expected: 全部 PASSED

- [ ] **Step 4: Commit**

```bash
git add projects/quant_alpha/plugin.py
git commit -m "feat: upgrade analyze_node to support IS/OOS dual-metrics and overfitting check"
```

---

### Task 8: freqtrade_cli.py

**Files:**
- Create: `projects/quant_alpha/freqtrade_cli.py`
- Modify: `tests/test_freqtrade_integration.py`

- [ ] **Step 1: 新增 freqtrade_cli 測試**

```python
# ── Task 8: freqtrade_cli ─────────────────────────────────────────────────────

class TestFreqtradeCli:
    def test_cli_backtest_dispatches_to_backtest_module(self, tmp_path, monkeypatch):
        """freqtrade_cli backtest subcommand calls run_backtest_is_oos."""
        monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path))
        spec_path = tmp_path / "spec.json"
        plan_path = tmp_path / "plan.json"
        spec_path.write_text(json.dumps(SAMPLE_SPEC), encoding="utf-8")
        plan_path.write_text(json.dumps(SAMPLE_PLAN), encoding="utf-8")

        fake_is  = {"win_rate": 0.6, "profit_factor": 1.5, "max_drawdown": 0.12,
                    "profit_total_pct": 25.0, "n_trades": 45, "trades": []}
        fake_oos = {"win_rate": 0.55, "profit_factor": 1.3, "max_drawdown": 0.14,
                    "profit_total_pct": 20.0, "n_trades": 38, "trades": []}

        with patch("projects.quant_alpha.freqtrade_cli.run_backtest_is_oos",
                   return_value=(fake_is, fake_oos)) as mock_bt, \
             patch("projects.quant_alpha.freqtrade_cli.write_loop_artifacts"):
            from projects.quant_alpha import freqtrade_cli
            freqtrade_cli.dispatch([
                "backtest",
                "--spec", str(spec_path),
                "--plan", str(plan_path),
                "--work-dir", str(tmp_path / "work"),
                "--userdir", str(tmp_path / "user_data"),
                "--loop", "0",
            ])

        mock_bt.assert_called_once()
```

- [ ] **Step 2: 執行測試，確認 FAIL**

```
pytest tests/test_freqtrade_integration.py::TestFreqtradeCli -v
```
Expected: ImportError

- [ ] **Step 3: 實作 freqtrade_cli.py**

```python
# projects/quant_alpha/freqtrade_cli.py
"""
freqtrade_cli.py — CLI entry point for Freqtrade subcommand dispatch.

LLM 在 implement_node 中透過 subprocess 呼叫此檔案：
    python projects/quant_alpha/freqtrade_cli.py backtest \
        --spec path/to/spec.json --plan path/to/plan_output.json \
        --work-dir path/to/work --userdir path/to/user_data --loop N

此檔案 import freqtrade_runner 和 result_parser（不重複實作 subprocess 邏輯）。
"""
import argparse
import json
import sys
from pathlib import Path

from projects.quant_alpha.backtest import run_backtest_is_oos
from projects.quant_alpha.result_parser import write_loop_artifacts


def dispatch(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="freqtrade_cli",
        description="Freqtrade subcommand dispatcher for agentic backtest loop",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # ── backtest ──────────────────────────────────────────────────────────────
    bt = sub.add_parser("backtest", help="Run IS/OOS backtest")
    bt.add_argument("--spec",     required=True, help="Path to spec JSON file")
    bt.add_argument("--plan",     required=True, help="Path to plan_output JSON file")
    bt.add_argument("--work-dir", required=True, dest="work_dir", help="Per-loop working directory")
    bt.add_argument("--userdir",  required=True, help="Freqtrade user_data directory")
    bt.add_argument("--loop",     required=True, type=int, help="Loop index (for artifact naming)")

    args = parser.parse_args(argv)

    if args.subcommand == "backtest":
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
        work_dir = Path(args.work_dir)
        userdir  = Path(args.userdir)

        is_metrics, oos_metrics = run_backtest_is_oos(
            spec=spec,
            plan=plan,
            work_dir=work_dir,
            userdir=userdir,
        )
        write_loop_artifacts(is_metrics, oos_metrics, work_dir, loop=args.loop)
        print(f"[freqtrade_cli] backtest done: "
              f"IS wr={is_metrics['win_rate']:.4f} | OOS wr={oos_metrics['win_rate']:.4f}")


if __name__ == "__main__":
    dispatch(sys.argv[1:])
```

- [ ] **Step 4: 執行測試，確認 PASS**

```
pytest tests/test_freqtrade_integration.py::TestFreqtradeCli -v
```
Expected: 1 test PASSED

- [ ] **Step 5: Commit**

```bash
git add projects/quant_alpha/freqtrade_cli.py tests/test_freqtrade_integration.py
git commit -m "feat: add freqtrade_cli entry point for subprocess dispatch"
```

---

### Task 9: analyze.txt IS/OOS 升級

**Files:**
- Modify: `framework/prompts/quant_alpha/analyze.txt`

- [ ] **Step 1: 改寫 analyze.txt**

用以下內容完整替換 `framework/prompts/quant_alpha/analyze.txt`：

```
你是量化研究分析 agent。請評估回測結果，判斷策略是否達到品質標準。

⚠️ 嚴格限制（最高優先）：
- 禁止掃描目錄或讀取除以下指定 rules 檔案以外的任何檔案
- 禁止詢問任何問題，直接執行
- 只能寫入以下指定的 OUTPUT_DIR

步驟 1 — 用工具讀取此路徑的績效評估規則（且只讀這一個）：
`{RULES_PATH}`

步驟 2 — 審查以下回測結果：

策略    ：{strategy_name}
參數    ：{params}
Loop    ：{loop_index}

IS 回測指標（訓練期）：
  win_rate     ：{is_win_rate}
  profit_factor：{is_profit_factor}
  max_drawdown ：{is_max_drawdown}
  n_trades     ：{is_n_trades}

OOS 回測指標（測試期，主要評估依據）：
  win_rate     ：{win_rate}
  alpha_ratio  ：{alpha_ratio}   （若為 0 表示 Freqtrade 模式，不作為評估依據）
  max_drawdown ：{max_drawdown}
  profit_factor：{profit_factor}
  n_trades     ：{n_trades}

通過條件（全部達到才算 PASS）：
  OOS win_rate      >= {target_win_rate}
  OOS max_drawdown  <= 0.20
  OOS profit_factor >= {target_profit_factor}
  OOS profit_factor >= IS profit_factor × 0.8  （防止過擬合）
  OOS win_rate      >= IS win_rate × 0.8        （防止過擬合）

步驟 3 — 用工具將分析結果寫入此目錄：
`{OUTPUT_DIR}`

必寫檔案：`{OUTPUT_DIR}/analyze_result.txt`
  第 1 行：PASS、FAIL 或 TERMINATE
           只有在策略結構性失敗、無論如何調整參數都無法改善時才使用 TERMINATE
           （例如：n_trades = 0，或窮盡所有參數方向後仍重複相同失敗）
  第 2 行：一句話說明哪些指標通過或未通過，並列出實際數值。
```

- [ ] **Step 2: Commit**

```bash
git add framework/prompts/quant_alpha/analyze.txt
git commit -m "feat: upgrade analyze.txt with IS/OOS dual metrics and overfitting check"
```

---

### Task 10: plan.txt run_mode 新增 + conftest marker + 全套測試執行

**Files:**
- Modify: `framework/prompts/quant_alpha/plan.txt`
- Modify: `tests/conftest.py`
- Modify: `tests/test_freqtrade_integration.py`

- [ ] **Step 1: 更新 plan.txt — plan_output.json 加入 run_mode 欄位**

在 `framework/prompts/quant_alpha/plan.txt` 的 `plan_output.json` 輸出範例區塊，找到：

```json
{{
  "strategy_name": "類別名稱（與 .py 檔名一致）",
  "strategy_file": "{STRATEGY_DIR}/{{strategy_name}}.py",
  "timeframe": "來自規格",
  "stoploss": 停損數值（負數浮點數）,
  "parameters": {{
    "各指標參數預設值": "值"
  }},
  "_reason": "本輪與上一輪的差異，或首輪說明策略依據"
}}
```

替換為：

```json
{{
  "strategy_name": "類別名稱（與 .py 檔名一致）",
  "strategy_file": "{STRATEGY_DIR}/{{strategy_name}}.py",
  "timeframe": "來自規格",
  "stoploss": 停損數值（負數浮點數）,
  "run_mode": "backtest",
  "parameters": {{
    "各指標參數預設值": "值"
  }},
  "_reason": "本輪與上一輪的差異，或首輪說明策略依據"
}}
```

並在說明文字區塊加入：

```
**注意 `run_mode`**：
- `"backtest"` — 標準 IS/OOS 雙組回測（預設，也是此 TODO A 階段唯一支援的模式）
- `"hyperopt"` — 後續 TODO B 支援
- `"cross_test"` — 後續 TODO B 支援
```

- [ ] **Step 2: 更新 conftest.py — 加入 freqtrade_real marker**

在 `tests/conftest.py` 的 `pytest_configure` 函式加入：

```python
    config.addinivalue_line(
        "markers",
        "freqtrade_real: mark test as requiring a live Freqtrade CLI installation",
    )
```

- [ ] **Step 3: 新增 freqtrade_real 標記的整合測試（預設跳過）**

在 `tests/test_freqtrade_integration.py` 末尾加入：

```python
# ── Task 10: freqtrade_real integration tests （預設跳過）────────────────────

_HAS_FREQTRADE = False
try:
    import subprocess as _sp
    _HAS_FREQTRADE = _sp.run(
        ["freqtrade", "--version"], capture_output=True, timeout=5
    ).returncode == 0
except Exception:
    pass

pytestmark_freqtrade = pytest.mark.skipif(
    not _HAS_FREQTRADE,
    reason="freqtrade CLI not installed — skipping freqtrade_real tests",
)


@pytest.mark.freqtrade_real
class TestFreqtradeRealIntegration:
    def test_config_generator_produces_valid_json(self, tmp_path):
        """Smoke test: generated config.json passes json.loads."""
        from projects.quant_alpha.config_generator import generate_config
        path = generate_config(SAMPLE_SPEC, tmp_path)
        cfg = json.loads(path.read_text(encoding="utf-8"))
        assert cfg["exchange"]["name"] == "binance"

    def test_result_parser_with_fixture_zip(self, tmp_path):
        """Smoke test: result_parser handles the fixture zip correctly."""
        from projects.quant_alpha.result_parser import parse_backtest_zip
        zip_path = tmp_path / "fixture.zip"
        zip_path.write_bytes(_make_fixture_zip("TestRsiStrategy"))
        metrics = parse_backtest_zip(zip_path, "TestRsiStrategy")
        assert 0 <= metrics["win_rate"] <= 1
        assert metrics["n_trades"] >= 0

    @pytest.mark.freqtrade_real
    def test_freqtrade_cli_backtest_real(self, tmp_path):
        """E2E: requires real Freqtrade install and downloaded data."""
        pytest.skip("Requires manual setup: freqtrade data + strategy file")
```

- [ ] **Step 4: 執行全套整合測試（mock only），確認全部通過**

```
pytest tests/test_freqtrade_integration.py -v -k "not freqtrade_real" 2>&1 | tail -30
```
Expected: 所有非 `freqtrade_real` 測試全部 PASSED

- [ ] **Step 5: 執行既有測試，確認零改動**

```
pytest tests/ -v -m "not integration" 2>&1 | tail -20
```
Expected: 全部 PASSED

- [ ] **Step 6: Commit**

```bash
git add framework/prompts/quant_alpha/plan.txt tests/conftest.py tests/test_freqtrade_integration.py
git commit -m "feat: add run_mode to plan.txt, freqtrade_real marker, and full test suite"
```

---

## 完成後驗證

```bash
# 1. 所有 mock 模式測試
pytest tests/ -v -m "not integration" 2>&1 | tail -30

# 2. 整合測試（需 DATABASE_URL）
pytest tests/ -v -m integration 2>&1 | tail -20

# 3. 新增的 freqtrade 整合測試（mock only）
pytest tests/test_freqtrade_integration.py -v -k "not freqtrade_real"
```

新增檔案清單：
- `projects/quant_alpha/config_generator.py`
- `projects/quant_alpha/freqtrade_runner.py`
- `projects/quant_alpha/result_parser.py`
- `projects/quant_alpha/freqtrade_cli.py`
- `tests/test_freqtrade_integration.py`

修改檔案清單：
- `projects/quant_alpha/backtest.py`
- `projects/quant_alpha/plugin.py`
- `framework/prompts/quant_alpha/analyze.txt`
- `framework/prompts/quant_alpha/plan.txt`
- `tests/conftest.py`
