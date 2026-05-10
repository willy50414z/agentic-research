"""implement_step — IS backtest run (mock or real)."""
from __future__ import annotations

import hashlib
import json
import logging
import random as _random
import shutil
from datetime import datetime
from pathlib import Path

from ..backtest import run_backtest_is_oos
from ..result_parser import write_loop_artifacts
from ._common import (
    ARTIFACTS_DIR,
    BACKTEST_MODE,
    _append_execution_log,
    _write_artifact,
)

logger = logging.getLogger(__name__)


def _mock_implement_result(state: dict) -> dict:
    n      = state.get("analyze_attempt", 0)
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
        "win_rate":         win_rate,
        "alpha_ratio":      alpha_ratio,
        "max_drawdown":     max_drawdown,
        "n_trades":         n_trades,
        "total_return":     total_return,
        "profit_total_pct": round(total_return * 100, 4),
        "profit_factor":    profit_factor,
    }

    artifact_path = str(ARTIFACTS_DIR / f"v{n}_train.json")
    _write_artifact(artifact_path, json.dumps(
        {"iteration": n, "plan": plan, "is_result": is_result}, indent=2))

    return {
        "is_metrics": is_result,
        "artifacts": state.get("artifacts", []) + [
            {"type": "train_result", "path": artifact_path}
        ],
    }


def _real_implement(state: dict) -> dict:
    n        = state.get("analyze_attempt", 0)
    plan     = state.get("implementation_plan", {}) or {}
    spec     = state.get("spec") or {}
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    work_dir  = ARTIFACTS_DIR / ".llm_io" / f"{n}_{timestamp}"
    userdir   = ARTIFACTS_DIR / "user_data"
    work_dir.mkdir(parents=True, exist_ok=True)
    userdir.mkdir(parents=True, exist_ok=True)

    strategy_file = plan.get("strategy_file", "")
    if strategy_file and Path(strategy_file).exists():
        strat_dest_dir = work_dir / "strategies"
        strat_dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(strategy_file, strat_dest_dir / Path(strategy_file).name)
        logger.info("[freqtrade] preserved strategy → %s", strat_dest_dir / Path(strategy_file).name)

    logger.info("[freqtrade] real implement  iteration=%d  work_dir=%s", n, work_dir)
    is_metrics, oos_metrics, is_zip, oos_zip = run_backtest_is_oos(
        spec=spec, plan=plan, work_dir=work_dir, userdir=userdir,
    )
    write_loop_artifacts(is_metrics, oos_metrics, work_dir, loop=n)
    _append_execution_log(n, plan, is_metrics, oos_metrics)

    is_zip_dest  = work_dir / f"v{n}_is.zip"
    oos_zip_dest = work_dir / f"v{n}_oos.zip"
    shutil.copy2(is_zip,  is_zip_dest)
    shutil.copy2(oos_zip, oos_zip_dest)

    return {
        "is_metrics":  is_metrics,
        "oos_metrics": oos_metrics,
        "artifacts": state.get("artifacts", []) + [
            {"type": "is_result",  "path": str(work_dir / f"v{n}_is.json")},
            {"type": "oos_result", "path": str(work_dir / f"v{n}_oos.json")},
            {"type": "trades",     "path": str(work_dir / f"v{n}_trades.json")},
            {"type": "signals",    "path": str(work_dir / f"v{n}_signals.json")},
            {"type": "report",     "path": str(work_dir / f"v{n}_report.html")},
            {"type": "is_zip",     "path": str(is_zip_dest)},
            {"type": "oos_zip",    "path": str(oos_zip_dest)},
        ],
    }


def implement_step(state: dict) -> dict:
    loop = state.get("loop_index", 0)
    plan = state.get("implementation_plan") or {}
    logger.info("[freqtrade] implement  loop=%d  strategy=%s", loop, plan.get("strategy_type"))

    if BACKTEST_MODE == "mock":
        return _mock_implement_result(state)
    return _real_implement(state)
