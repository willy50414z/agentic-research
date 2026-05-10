"""plan_step — LLM designs a strategy from spec.raw_md."""
from __future__ import annotations

import logging
from pathlib import Path

from ._common import (
    ARTIFACTS_DIR,
    _call_llm,
    _load_prompt,
    _read_json_file,
)

logger = logging.getLogger(__name__)


def plan_step(state: dict) -> dict:
    loop = state.get("loop_index", 0)
    goal = state.get("loop_goal", "find alpha in momentum strategies")
    logger.info("[freqtrade] plan  loop=%d", loop)

    output_dir   = str(ARTIFACTS_DIR.resolve())
    strategy_dir = str((ARTIFACTS_DIR / "strategies").resolve())
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

    return {
        "loop_goal":            goal,
        "implementation_plan":  plan,
        "needs_human_approval": False,
        "last_result":          "PLAN_READY",
        "last_reason":          f"Plan: {plan.get('strategy_name', '?')}.",
    }
