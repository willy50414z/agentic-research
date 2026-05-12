# app/freqtrade/backtest.py
"""
Real Freqtrade backtest IS/OOS orchestrator.
Calls config_generator, runner, result_parser.

Usage:
    from app.freqtrade.backtest import run_backtest_is_oos

    is_metrics, oos_metrics = run_backtest_is_oos(spec, plan, work_dir, userdir)
    # Each metrics dict: {win_rate, profit_factor, max_drawdown,
    #                     profit_total_pct, n_trades, trades}
"""
from pathlib import Path
from typing import Any

from .config_generator import generate_config
from .runner import download_data, run_freqtrade_backtest
from .result_parser import parse_backtest_zip


def run_backtest_is_oos(
    spec: dict[str, Any],
    plan: dict[str, Any],
    work_dir: Path,
    userdir: Path,
) -> tuple[dict[str, Any], dict[str, Any], Path, Path]:
    """
    Run IS and OOS backtests via Freqtrade CLI.
    Returns (is_metrics, oos_metrics, is_zip, oos_zip).

    IS timerange  ← spec["data"]["train_period"]
    OOS timerange ← spec["data"]["test_period"]
    """
    strategy_name = plan.get("strategy_name")
    if not strategy_name:
        raise ValueError(f"implementation_plan missing 'strategy_name': {plan}")
    strategy_file = plan.get("strategy_file")
    if strategy_file:
        if not Path(strategy_file).exists():
            raise FileNotFoundError(
                f"strategy_file '{strategy_file}' does not exist on disk; "
                "plan_step LLM may have failed to write the .py — "
                "check LLM_FREQTRADE_STEPS in .env and review plan_step logs"
            )
        strategy_dir = str(Path(strategy_file).parent)
        # Defensive: per per-iteration-strategy-snapshot spec, freqtrade SHALL
        # never read from artifacts/.staging — that path holds non-promoted
        # candidate strategies. If it leaked into plan.strategy_file, fail
        # loudly rather than running an unaudited .py.
        if ".staging" in strategy_dir.replace("\\", "/").split("/"):
            raise ValueError(
                f"strategy_file points into .staging ({strategy_file}); "
                "only audit-promoted strategies under artifacts/strategies/v{N}/ "
                "may be backtested"
            )
    else:
        strategy_dir = str(work_dir / "strategies")
    results_dir   = str(userdir / "backtest_results")

    config_path = generate_config(spec, work_dir, plan=plan)

    data = spec.get("data") or {}
    universe = spec.get("universe") or {}
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

    scope    = spec.get("trading_scope") or spec.get("universe") or {}
    pair      = scope.get("pair") or scope.get("instruments", "")
    exchange  = scope.get("exchange", "")
    timeframe = scope.get("timeframe", "")
    _ensure_data(userdir, config_path, exchange, pair, timeframe, is_range, oos_range)

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
    return is_metrics, oos_metrics, is_zip, oos_zip


def _to_freqtrade_timerange(period: dict[str, str]) -> str:
    """{"start": "2023-01-01", "end": "2023-12-31"} → "20230101-20231231" """
    start = str(period["start"]).replace("-", "")
    end   = str(period["end"]).replace("-", "")
    return f"{start}-{end}"


def _ensure_data(
    userdir: Path,
    config_path: Path,
    exchange: str,
    pair: str,
    timeframe: str,
    is_range: str,
    oos_range: str,
) -> None:
    """Download market data covering IS+OOS before backtesting, then verify."""
    import logging
    logger = logging.getLogger(__name__)

    full_range = f"{is_range[:8]}-{oos_range[9:]}"
    pair_norm = pair.replace("/", "_")

    logger.info("[backtest] _ensure_data: exchange=%s pair=%s timeframe=%s is_range=%s oos_range=%s full_range=%s",
                exchange, pair, timeframe, is_range, oos_range, full_range)
    logger.info("[backtest] _ensure_data: looking for %s/%s-%s.* in %s",
                exchange.lower(), pair_norm, timeframe.lower(), userdir / "data" / exchange.lower())

    download_data(str(config_path), str(userdir), full_range, timeframe=timeframe)

    data_dir  = userdir / "data" / exchange.lower()
    logger.info("[backtest] _ensure_data: verifying data in %s", data_dir)

    has_data  = any(
        (data_dir / f"{pair_norm}-{timeframe.lower()}.{ext}").exists()
        for ext in ("json", "feather", "json.gz")
    )
    if has_data:
        logger.info("[backtest] _ensure_data: found data file for %s-%s", pair_norm, timeframe.lower())
    else:
        existing_files = list(data_dir.glob("*")) if data_dir.exists() else []
        logger.warning("[backtest] _ensure_data: NO data file found, existing files: %s", [f.name for f in existing_files[:10]])
        raise FileNotFoundError(
            f"freqtrade download-data completed but no data file found at "
            f"{data_dir}/{pair_norm}-{timeframe.lower()}.*\n"
            f"Check network access and exchange availability."
        )
