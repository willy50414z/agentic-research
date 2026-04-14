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

    def test_generate_config_fee_already_float(self, tmp_path):
        from projects.quant_alpha.config_generator import generate_config
        spec = {**SAMPLE_SPEC, "execution": {"fee": 0.001}}
        path = generate_config(spec, tmp_path)
        cfg = json.loads(path.read_text(encoding="utf-8"))
        assert abs(cfg["fee"] - 0.001) < 1e-9


# ── Task 2: freqtrade_runner ──────────────────────────────────────────────────

class TestFreqtradeRunner:
    def test_success_returns_zip_path(self, tmp_path):
        """Mock subprocess success — returns newest .zip in results_dir."""
        from projects.quant_alpha.freqtrade_runner import run_freqtrade_backtest
        results_dir = tmp_path / "backtest_results"
        results_dir.mkdir()
        zip_path = results_dir / "backtest-result-2024-01-01_00-00-00.zip"

        def _fake_run(*args, **kwargs):
            zip_path.write_bytes(b"PK")  # create zip when subprocess "runs"
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=_fake_run):
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
