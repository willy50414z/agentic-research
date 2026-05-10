"""summarize_step — LLM writes a per-loop research report when the loop PASSes."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ._common import (
    ARTIFACTS_DIR,
    _call_llm,
    _load_prompt,
    _write_artifact,
)

logger = logging.getLogger(__name__)


def summarize_step(state: dict) -> dict:
    loop    = state.get("loop_index", 0)
    plan    = state.get("implementation_plan") or {}
    metrics = state.get("test_metrics", {})
    goal    = state.get("loop_goal", "")
    logger.info("[freqtrade] summarize  loop=%d", loop)

    output_dir = str(ARTIFACTS_DIR.resolve())
    prompt = _load_prompt("summarize").format(
        project_id    = state.get("project_id", "?"),
        goal          = goal,
        loop_index    = loop,
        strategy_name = plan.get("strategy_name", "?"),
        params        = json.dumps(plan, ensure_ascii=False, indent=2),
        win_rate      = metrics.get("win_rate", 0),
        alpha_ratio   = metrics.get("alpha_ratio", 0),
        max_drawdown  = metrics.get("max_drawdown", 0),
        profit_factor = metrics.get("profit_factor", 0.0),
        n_trades      = metrics.get("n_trades", 0),
        total_return  = metrics.get("total_return", 0),
        OUTPUT_DIR    = output_dir,
    )

    report_md = ""
    summary   = ""
    try:
        _call_llm(prompt, cwd=output_dir)
        summary_file = Path(output_dir) / "loop_summary.md"
        report_md = summary_file.read_text(encoding="utf-8").strip()
        first_line = report_md.splitlines()[0] if report_md else ""
        summary = first_line.strip("_").replace("摘要：", "").strip() if "摘要" in first_line else ""
    except (FileNotFoundError, RuntimeError) as e:
        logger.warning("[freqtrade] summarize  LLM unavailable (%s) — generating report", e)
    except Exception as e:
        logger.warning("[freqtrade] summarize  file read failed (%s) — generating report", e)

    if not report_md:
        report_md = (
            f"# Loop {loop} Research Report\n\n"
            f"**Strategy** : {plan.get('strategy_type', '?')}\n"
            f"**Goal**     : {goal}\n\n"
            f"## Results\n\n"
            f"| Metric | Value |\n|---|---|\n"
            f"| win_rate     | {metrics.get('win_rate', 0):.4f} |\n"
            f"| alpha_ratio  | {metrics.get('alpha_ratio', 0):.4f} |\n"
            f"| max_drawdown | {metrics.get('max_drawdown', 0):.4f} |\n"
            f"| n_trades     | {metrics.get('n_trades', 0)} |\n"
            f"| total_return | {metrics.get('total_return', 0):.4f} |\n"
        )
        summary = (
            f"Loop {loop} PASS: win_rate={metrics.get('win_rate',0):.4f} "
            f"alpha={metrics.get('alpha_ratio',0):.4f}"
        )

    artifact_path = str(ARTIFACTS_DIR / f"loop_{loop}_report.md")
    _write_artifact(artifact_path, report_md)

    return {
        "loop_index":    loop + 1,
        "last_reason":   summary,
        "attempt_count": 0,
        "artifacts": state.get("artifacts", []) + [
            {"type": "summary", "path": artifact_path}
        ],
    }
