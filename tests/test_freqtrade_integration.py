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


# ── Task 6: real implement_node ───────────────────────────────────────────────

class TestRealImplementNode:
    def test_real_implement_runs_is_oos(self, tmp_path, monkeypatch):
        """Real implement_node calls run_backtest_is_oos and write_loop_artifacts."""
        import projects.quant_alpha.plugin as plugin_mod

        # Override BACKTEST_MODE and ARTIFACTS_DIR at the module level for this test
        monkeypatch.setattr(plugin_mod, "BACKTEST_MODE", "real")
        monkeypatch.setattr(plugin_mod, "ARTIFACTS_DIR", tmp_path)

        fake_is  = {"win_rate": 0.6, "profit_factor": 1.5, "max_drawdown": 0.12,
                    "profit_total_pct": 25.0, "n_trades": 45, "trades": []}
        fake_oos = {"win_rate": 0.55, "profit_factor": 1.3, "max_drawdown": 0.14,
                    "profit_total_pct": 20.0, "n_trades": 38, "trades": []}

        with patch("projects.quant_alpha.plugin.run_backtest_is_oos",
                   return_value=(fake_is, fake_oos)) as mock_bt, \
             patch("projects.quant_alpha.plugin.write_loop_artifacts"), \
             patch("projects.quant_alpha.plugin._append_execution_log"):
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


# ── Task 8: freqtrade_cli ─────────────────────────────────────────────────────

class TestFreqtradeCli:
    def test_cli_backtest_dispatches_to_backtest_module(self, tmp_path):
        """freqtrade_cli backtest subcommand calls run_backtest_is_oos."""
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
