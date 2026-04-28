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

    data = spec.get("data") or {}
    universe = spec.get("universe") or {}
    # Fallback: build period dicts from legacy universe flat date strings
    train_period = data.get("train_period") or {
        "start": universe.get("train_start", ""),
        "end":   universe.get("train_end", ""),
    }
    test_period = data.get("test_period") or {
        "start": universe.get("test_start", ""),
        "end":   universe.get("test_end", ""),
    }
    is_range  = _to_freqtrade_timerange(train_period)
    oos_range = _to_freqtrade_timerange(test_period)

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
