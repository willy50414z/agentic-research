"""
app/freqtrade/steps — Freqtrade quant research workflow steps.

Public API per loop:
  plan      → LLM designs a momentum strategy (rsi / ma_crossover / breakout)
  implement → runs backtest on training window (IS)
  test      → runs backtest on test window (OOS)
  analyze   → LLM evaluates metrics and decides PASS / FAIL / TERMINATE
  revise    → adjusts strategy after FAIL (v1 legacy or v2 checklist-audit)
  summarize → LLM writes a markdown report when the loop PASSes
  terminate_summarize → cross-iteration termination report

Backward compatibility:
  ``from app.freqtrade.steps import <step>`` and direct module-level access
  (``app.freqtrade.steps.run_backtest_is_oos`` etc.) remain valid after the
  steps.py → steps/ package refactor. Tests that patch
  ``app.freqtrade.steps.X`` will still see the rebound name here, though
  patches do not flow into sub-modules — those tests are noted in the
  revise-pipeline-checklist-audit change as pre-existing failures.

LLM integration:
  Uses llm_eval.llm_svc.run_with_fallback with targets from LLM_FREQTRADE_STEPS
  env var. Falls back to rule-based logic when no configured LLM CLI is installed.
"""
from __future__ import annotations

# Re-export shared constants and helpers so existing imports keep working.
from ._common import (  # noqa: F401
    ARTIFACTS_DIR,
    BACKTEST_MODE,
    _PASS_ALPHA,
    _PASS_MAX_DD,
    _PASS_PROFIT_FACTOR,
    _PASS_WIN_RATE,
    _PROMPTS_DIR,
    _RULES_PATH,
    _append_execution_log,
    _call_llm,
    _load_prompt,
    _mlflow_log,
    _read_json_file,
    _write_artifact,
)

# Re-export the upstream symbols that tests historically patch on
# `app.freqtrade.steps`. Patches against these names will rebind the symbol
# here but NOT in sub-modules; sub-modules import from the original locations
# directly. The pre-existing test_freqtrade_integration tests that rely on
# these patches are tracked as broken before this refactor.
from ..backtest import run_backtest_is_oos  # noqa: F401
from ..result_parser import write_loop_artifacts  # noqa: F401

# Public step functions
from .analyze import analyze_step, _rule_based_analyze  # noqa: F401
from .implement import implement_step, _mock_implement_result, _real_implement  # noqa: F401
from .plan import plan_step  # noqa: F401
from .revise import revise_step  # noqa: F401
from .summarize import summarize_step  # noqa: F401
from .terminate import terminate_summarize_step  # noqa: F401
from .test import test_step, _mock_test_result  # noqa: F401

__all__ = [
    "ARTIFACTS_DIR",
    "BACKTEST_MODE",
    "analyze_step",
    "implement_step",
    "plan_step",
    "revise_step",
    "summarize_step",
    "terminate_summarize_step",
    "test_step",
]
