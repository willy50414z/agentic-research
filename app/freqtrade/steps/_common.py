"""
app/freqtrade/steps/_common.py

Shared constants and helpers used across the freqtrade workflow step modules.

Public re-exports (via app.freqtrade.steps.__init__) include:
  - ARTIFACTS_DIR
  - _call_llm
  - _append_execution_log (referenced by tests via app.freqtrade.steps._append_execution_log)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path

import mlflow

from llm_eval.llm_svc import run_with_fallback
from app.llm import get_llm_targets

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants — shared across step modules
# ---------------------------------------------------------------------------

ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "./artifacts"))
BACKTEST_MODE = os.getenv("BACKTEST_MODE", "mock")

_PROMPTS_DIR = Path(__file__).parent.parent.parent / "prompts" / "freqtrade"

_PASS_WIN_RATE      = 0.55
_PASS_ALPHA         = 1.0
_PASS_MAX_DD        = 0.20
_PASS_PROFIT_FACTOR = 1.2

_RULES_PATH = str(
    (Path(__file__).parent.parent.parent.parent / ".ai" / "rules" / "backtest-required-metrics.md").resolve()
)

_MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI")
if _MLFLOW_URI:
    mlflow.set_tracking_uri(_MLFLOW_URI)


# ---------------------------------------------------------------------------
# Prompt loading + LLM call
# ---------------------------------------------------------------------------


def _load_prompt(name: str) -> str:
    return (_PROMPTS_DIR / f"{name}.txt").read_text(encoding="utf-8")


def _call_llm(prompt: str, cwd: str | None = None) -> str:
    return run_with_fallback(get_llm_targets("freqtrade_steps"), prompt, timeout=1200, cwd=cwd)


# ---------------------------------------------------------------------------
# Artifact / file helpers
# ---------------------------------------------------------------------------


def _read_json_file(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not read JSON from '%s': %s", path, e)
        return {}


def _write_artifact(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _append_execution_log(
    loop: int, plan: dict, is_metrics: dict, oos_metrics: dict, result: str = "—",
) -> None:
    log_path = ARTIFACTS_DIR / "execution_log.md"
    ts       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"| {ts} | loop {loop} | {plan.get('strategy_name', '?')} | {plan.get('run_mode', 'backtest')} "
        f"| IS pf={is_metrics.get('profit_factor', 0):.3f} wr={is_metrics.get('win_rate', 0):.3f} "
        f"| OOS pf={oos_metrics.get('profit_factor', 0):.3f} wr={oos_metrics.get('win_rate', 0):.3f} "
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
        logger.warning("[freqtrade] execution_log write failed: %s", e)


# ---------------------------------------------------------------------------
# MLflow logging
# ---------------------------------------------------------------------------


def _mlflow_log(project_id: str, loop: int, plan: dict, metrics: dict, result: str) -> None:
    if not _MLFLOW_URI:
        return
    try:
        mlflow.set_experiment(project_id)
        with mlflow.start_run(run_name=f"loop_{loop}"):
            mlflow.log_param("strategy_type",   plan.get("strategy_type", "unknown"))
            mlflow.log_param("lookback",         plan.get("lookback"))
            mlflow.log_param("entry_threshold",  plan.get("entry_threshold"))
            mlflow.log_param("exit_threshold",   plan.get("exit_threshold"))
            mlflow.log_param("stop_loss_pct",    plan.get("stop_loss_pct"))
            mlflow.log_param("loop_result",      result)
            mlflow.log_metric("win_rate",      metrics.get("win_rate", 0))
            mlflow.log_metric("alpha_ratio",   metrics.get("alpha_ratio", 0))
            mlflow.log_metric("max_drawdown",  metrics.get("max_drawdown", 0))
            mlflow.log_metric("n_trades",      metrics.get("n_trades", 0))
            mlflow.log_metric("total_return",  metrics.get("total_return", 0))
    except Exception as e:
        logger.warning("[freqtrade] mlflow log failed (loop=%d): %s", loop, e)
