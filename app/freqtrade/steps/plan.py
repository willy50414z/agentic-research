"""plan_step — LLM designs a strategy from spec.raw_md.

The baseline strategy ``.py`` is written directly to
``artifacts/strategies/v0/{StrategyName}.py`` (no staging path — baseline does
not undergo audit). After the LLM produces ``plan_output.json``, plan_step
also writes ``v0_strategy_spec.md`` from the AST of that ``.py`` (per the
``per-iteration-strategy-snapshot`` capability).
"""
from __future__ import annotations

import logging
from pathlib import Path

from ._common import (
    ARTIFACTS_DIR,
    _call_llm,
    _load_prompt,
    _read_json_file,
)
from .strategy_snapshot import write_strategy_spec_snapshot

logger = logging.getLogger(__name__)


def plan_step(state: dict) -> dict:
    loop = state.get("loop_index", 0)
    goal = state.get("loop_goal", "find alpha in momentum strategies")
    logger.info("[freqtrade] plan  loop=%d", loop)

    output_dir   = str(ARTIFACTS_DIR.resolve())
    # Baseline lives in v0/ directly (no staging — baseline isn't audited).
    strategy_dir = str((ARTIFACTS_DIR / "strategies" / "v0").resolve())
    spec         = state.get("spec") or {}
    spec_md      = spec.get("raw_md", "（spec 未提供）")
    if spec_md == "（spec 未提供）":
        raise RuntimeError(
            "spec.raw_md is empty — spec review may have failed to generate reviewed_spec_final.md. "
            "Complete spec review first."
        )
    timeframe    = (spec.get("trading_scope") or spec.get("universe") or {}).get("timeframe", "1d")

    prompt = _load_prompt("plan").format(
        SPEC          = spec_md,
        TIMEFRAME     = timeframe,
        loop_index    = loop,
        last_decision = state.get("last_reason", "none"),
        STRATEGY_DIR  = strategy_dir,
        OUTPUT_DIR    = output_dir,
    )

    plan = {}
    try:
        _call_llm(prompt, cwd=output_dir)
        plan = _read_json_file(Path(output_dir) / "plan_output.json")
        logger.info("[freqtrade] plan  LLM strategy=%s", plan.get("strategy_name"))
    except (FileNotFoundError, RuntimeError) as e:
        logger.warning("[freqtrade] plan  LLM unavailable (%s) — using fallback", e)
    if not plan:
        plan = {"strategy_name": "FallbackRsiMomentum", "stoploss": -0.05, "parameters": {}}

    plan.setdefault("strategy_name", "UnknownStrategy")
    plan.setdefault("stoploss",      -0.05)

    # Snapshot v0_strategy_spec.md from the freshly-written .py if present.
    artifacts: list[dict] = list(state.get("artifacts") or [])
    py_path = plan.get("strategy_file") or str(
        Path(strategy_dir) / f"{plan['strategy_name']}.py"
    )
    # Only record strategy_file when the .py was actually written to disk.
    # If the LLM fallback was used, the file does not exist; leaving strategy_file
    # unset causes backtest.py to look in work_dir/strategies/ rather than
    # forwarding a phantom path that makes freqtrade exit with a cryptic error.
    if Path(py_path).exists():
        plan["strategy_file"] = py_path
    else:
        plan.pop("strategy_file", None)
    snapshot_path = ARTIFACTS_DIR / "v0_strategy_spec.md"
    try:
        if Path(py_path).exists():
            write_strategy_spec_snapshot(
                py_path=py_path,
                prev_py_path=None,
                checklist=None,
                intent_md=None,
                output_path=snapshot_path,
            )
            artifacts.append({"type": "strategy_spec", "path": str(snapshot_path)})
        else:
            logger.warning(
                "[freqtrade] plan  strategy_file '%s' not on disk — skipping snapshot",
                py_path,
            )
    except Exception as e:
        # Snapshot failure is non-fatal; log and continue.
        logger.warning("[freqtrade] plan  strategy_spec snapshot failed: %s", e)

    return {
        "loop_goal":            goal,
        "implementation_plan":  plan,
        "needs_human_approval": False,
        "last_result":          "PLAN_READY",
        "last_reason":          f"Plan: {plan.get('strategy_name', '?')}.",
        "artifacts":            artifacts,
    }
