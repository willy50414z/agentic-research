"""terminate_summarize_step — LLM writes a cross-iteration termination report."""
from __future__ import annotations

import logging
from pathlib import Path

from ._common import (
    ARTIFACTS_DIR,
    _PASS_WIN_RATE,
    _call_llm,
    _load_prompt,
    _write_artifact,
)

logger = logging.getLogger(__name__)


def terminate_summarize_step(state: dict) -> dict:
    loop      = state.get("loop_index", 0)
    plan      = state.get("implementation_plan") or {}
    metrics   = state.get("test_metrics") or {}
    goal      = state.get("loop_goal", "")
    reason    = state.get("last_reason", "Max attempts reached.")
    attempt   = state.get("attempt_count", 0)
    artifacts = list(state.get("artifacts") or [])
    logger.info("[freqtrade] terminate_summarize  loop=%d", loop)

    output_dir = str(ARTIFACTS_DIR.resolve())
    attempts_lines = [f"  - {a['path']}" for a in artifacts if a.get("type") == "train_result"]
    if metrics:
        attempts_lines.append(
            f"  - final: win_rate={metrics.get('win_rate',0):.4f} "
            f"alpha={metrics.get('alpha_ratio',0):.4f} "
            f"drawdown={metrics.get('max_drawdown',0):.4f}"
        )
    attempts_table = "\n".join(attempts_lines) or "  (no attempts recorded)"

    prompt = _load_prompt("terminate_summary").format(
        project_id       = state.get("project_id", "?"),
        goal             = goal,
        strategy_name    = plan.get("strategy_name", "?"),
        terminate_reason = reason,
        attempt_count    = attempt,
        attempts_table   = attempts_table,
        target_win_rate  = plan.get("target_win_rate", _PASS_WIN_RATE),
        OUTPUT_DIR       = output_dir,
    )

    report_md = ""
    summary   = ""
    try:
        _call_llm(prompt, cwd=output_dir)
        report_file = Path(output_dir) / "termination_report.md"
        report_md = report_file.read_text(encoding="utf-8").strip()
        for line in report_md.splitlines():
            if "摘要" in line:
                summary = line.strip("_").replace("摘要：", "").strip()
                break
    except (FileNotFoundError, RuntimeError) as e:
        logger.warning("[freqtrade] terminate_summarize  LLM unavailable (%s)", e)
    except Exception as e:
        logger.warning("[freqtrade] terminate_summarize  file read failed (%s)", e)

    if not report_md:
        report_md = (
            f"# Termination Report — Loop {loop}\n\n"
            f"**Strategy**: {plan.get('strategy_type', '?')}\n"
            f"**Goal**: {goal}\n"
            f"**Reason**: {reason}\n\n"
            f"## Final Metrics\n\n"
            f"| Metric | Value |\n|---|---|\n"
            f"| win_rate     | {metrics.get('win_rate', 0):.4f} |\n"
            f"| alpha_ratio  | {metrics.get('alpha_ratio', 0):.4f} |\n"
            f"| max_drawdown | {metrics.get('max_drawdown', 0):.4f} |\n"
            f"| n_trades     | {metrics.get('n_trades', 0)} |\n\n"
            f"## Next Steps\n\nReview parameters and restart from Planning.\n"
        )
        summary = f"Loop {loop} TERMINATE: {reason}"

    artifact_path = str(ARTIFACTS_DIR / f"loop_{loop}_terminate_report.md")
    _write_artifact(artifact_path, report_md)

    return {
        "last_reason": summary or f"Loop {loop} TERMINATE: {reason}",
        "artifacts": artifacts + [{"type": "terminate_summary", "path": artifact_path}],
    }
