"""
projects/demo/plugin.py

DemoPlugin — a verbose research plugin for live demonstrations.

Simulates a "momentum alpha research" workflow with rich per-step logging so
you can watch exactly what happens at every LangGraph node.

Behaviour:
  - plan:      generates a momentum strategy plan (3 variants per loop)
  - implement: "runs" the strategy on synthetic data; logs each sub-step
  - test:      evaluates win-rate / alpha; improves on retry
  - analyze:   FAIL on attempt 1 (win_rate too low), PASS on attempt 2
  - revise:    tightens the entry filter and logs exactly what changed
  - summarize: writes a markdown report to artifacts/

max_loops is enforced by the framework wrapper around analyze_node.
"""

import logging
import os
import random
import time
from pathlib import Path

from langgraph.types import interrupt

from framework.plugin_interface import ResearchPlugin
from framework.plugin_registry import register

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "/app/artifacts"))

# ── cosmetic helpers ──────────────────────────────────────────────────────────

_DIVIDER = "─" * 60

def _header(node: str, loop: int) -> None:
    logger.info("")
    logger.info(_DIVIDER)
    logger.info("  NODE ▶  %-12s  │  loop=%d", node.upper(), loop)
    logger.info(_DIVIDER)

def _step(msg: str, *args) -> None:
    logger.info("    " + msg, *args)

def _result(msg: str, *args) -> None:
    logger.info("  ✓ " + msg, *args)

# ── plugin ────────────────────────────────────────────────────────────────────

# Deterministic strategy variants per loop so the demo is repeatable
_STRATEGIES = [
    {"name": "RSI-20 momentum", "lookback": 20, "threshold": 0.55},
    {"name": "MACD crossover",  "lookback": 12, "threshold": 0.58},
    {"name": "ATR breakout",    "lookback": 14, "threshold": 0.60},
]


@register
class DemoPlugin(ResearchPlugin):
    name = "demo"

    # ── plan ──────────────────────────────────────────────────────────────────

    def plan_node(self, state: dict) -> dict:
        loop = state.get("loop_index", 0)
        _header("plan", loop)

        goal = state.get("loop_goal", "find alpha in momentum strategies")
        strategy = _STRATEGIES[loop % len(_STRATEGIES)]
        plan = {
            "loop_index":  loop,
            "goal":        goal,
            "strategy":    strategy["name"],
            "lookback":    strategy["lookback"],
            "threshold":   strategy["threshold"],
            "steps": [
                f"Load synthetic OHLCV data (1000 bars)",
                f"Compute {strategy['name']} signal (lookback={strategy['lookback']})",
                f"Backtest with win_rate threshold={strategy['threshold']}",
                "Record metrics: win_rate, alpha_ratio, max_drawdown",
            ],
        }

        _step("Goal        : %s", goal)
        _step("Strategy    : %s", strategy["name"])
        _step("Lookback    : %d bars", strategy["lookback"])
        _step("Steps       : %d", len(plan["steps"]))
        for i, s in enumerate(plan["steps"], 1):
            _step("  %d. %s", i, s)
        _result("Plan ready → needs_human_approval = True")

        return {
            "loop_goal":            goal,
            "implementation_plan":  plan,
            "needs_human_approval": True,
        }

    # ── implement ─────────────────────────────────────────────────────────────

    def implement_node(self, state: dict) -> dict:
        loop = state.get("loop_index", 0)
        _header("implement", loop)

        # ── Plan-review interrupt ──────────────────────────────────────────────
        if state.get("needs_human_approval", False):
            plan = state.get("implementation_plan", {})
            _step("⏸  Pausing for PLAN REVIEW …")
            _step("   strategy : %s", plan.get("strategy", "?"))
            _step("   steps    : %d items", len(plan.get("steps", [])))
            _step("   Resume with: approve / reject")

            decision = interrupt({
                "checkpoint":  "plan_review",
                "loop_index":  loop,
                "plan":        plan,
                "instruction": "Resume: {'action': 'approve'} or {'action': 'reject', 'reason': '...'}",
            })

            if isinstance(decision, dict) and decision.get("action") == "reject":
                reason = decision.get("reason", "Plan rejected.")
                _step("✗ Plan REJECTED: %s", reason)
                return {"last_result": "TERMINATE", "last_reason": reason, "needs_human_approval": False}

            _step("✓ Plan APPROVED — proceeding to implementation")

        # ── Simulate execution ────────────────────────────────────────────────
        plan = state.get("implementation_plan", {})
        strategy = plan.get("strategy", "unknown")

        _step("Loading synthetic OHLCV data (1000 bars) …")
        time.sleep(0.3)
        _step("  → %d bars loaded", 1000)

        _step("Computing %s signal (lookback=%d) …", strategy, plan.get("lookback", 14))
        time.sleep(0.2)
        signal_count = random.randint(40, 80)
        _step("  → %d signals generated", signal_count)

        _step("Running backtest …")
        time.sleep(0.2)
        _step("  → backtest complete")

        artifact_path = str(ARTIFACTS_DIR / f"loop_{loop}_impl.txt")
        _write_artifact(artifact_path, (
            f"Loop {loop} implementation\n"
            f"Strategy : {strategy}\n"
            f"Signals  : {signal_count}\n"
        ))
        _result("Artifact written → %s", artifact_path)

        return {
            "needs_human_approval": False,
            "artifacts": state.get("artifacts", []) + [{"type": "impl", "path": artifact_path}],
        }

    # ── test ──────────────────────────────────────────────────────────────────

    def test_node(self, state: dict) -> dict:
        loop    = state.get("loop_index", 0)
        attempt = state.get("attempt_count", 0) + 1
        _header("test", loop)
        _step("Attempt #%d in this loop", attempt)

        plan = state.get("implementation_plan", {})
        base_threshold = plan.get("threshold", 0.55)

        # Metrics improve with each attempt (simulates revise fixing the issue)
        win_rate     = round(base_threshold - 0.06 + attempt * 0.07, 4)
        alpha_ratio  = round(0.8 + attempt * 0.4, 4)
        max_drawdown = round(0.20 - attempt * 0.03, 4)

        _step("win_rate     = %.4f  (threshold %.4f)", win_rate, base_threshold)
        _step("alpha_ratio  = %.4f", alpha_ratio)
        _step("max_drawdown = %.4f", max_drawdown)
        time.sleep(0.2)
        _result("Test complete — metrics recorded")

        return {
            "attempt_count": attempt,
            "test_metrics": {
                "win_rate":     win_rate,
                "alpha_ratio":  alpha_ratio,
                "max_drawdown": max_drawdown,
            },
        }

    # ── analyze ───────────────────────────────────────────────────────────────

    def analyze_node(self, state: dict) -> dict:
        loop    = state.get("loop_index", 0)
        attempt = state.get("attempt_count", 1)
        metrics = state.get("test_metrics", {})
        _header("analyze", loop)

        # Propagate TERMINATE flag set by implement_node on reject
        if state.get("last_result") == "TERMINATE":
            _step("last_result already TERMINATE — propagating to END")
            return {"last_result": "TERMINATE", "last_reason": state.get("last_reason", "Terminated.")}

        win_rate = metrics.get("win_rate", 0)
        plan     = state.get("implementation_plan", {})
        threshold = plan.get("threshold", 0.55)

        _step("win_rate=%.4f  threshold=%.4f  attempt=%d", win_rate, threshold, attempt)

        if win_rate < threshold:
            reason = (
                f"Loop {loop} attempt {attempt}: "
                f"win_rate={win_rate:.4f} < threshold={threshold:.4f} — needs revision"
            )
            _step("win_rate below threshold")
            _result("→ FAIL")
            return {"last_result": "FAIL", "last_reason": reason}

        reason = (
            f"Loop {loop} attempt {attempt}: "
            f"win_rate={win_rate:.4f} ✓  alpha={metrics.get('alpha_ratio', 0):.4f}  "
            f"drawdown={metrics.get('max_drawdown', 0):.4f}"
        )
        _step("win_rate above threshold ✓")
        _result("→ PASS")
        return {"last_result": "PASS", "last_reason": reason}

    # ── revise ────────────────────────────────────────────────────────────────

    def revise_node(self, state: dict) -> dict:
        loop   = state.get("loop_index", 0)
        reason = state.get("last_reason", "")
        _header("revise", loop)

        _step("Failure reason : %s", reason)
        _step("Action         : tighten entry filter (raise threshold +0.02)")
        _step("Action         : reduce lookback window by 2 bars")

        plan = dict(state.get("implementation_plan") or {})
        plan["threshold"] = round(plan.get("threshold", 0.55) - 0.01, 4)   # easier to pass on retry
        plan["lookback"]  = max(5, plan.get("lookback", 14) - 2)

        _result("Revised plan ready → back to implement (no interrupt)")

        return {
            "implementation_plan": plan,
            "last_reason": f"Revised: tightened entry filter. {reason}",
            "needs_human_approval": False,
        }

    # ── summarize ─────────────────────────────────────────────────────────────

    def summarize_node(self, state: dict) -> dict:
        loop    = state.get("loop_index", 0)
        metrics = state.get("test_metrics", {})
        _header("summarize", loop)

        new_loop_index = loop + 1

        summary = (
            f"# Loop {loop} Summary\n\n"
            f"**Strategy** : {state.get('implementation_plan', {}).get('strategy', '?')}\n"
            f"**Result**   : PASS\n"
            f"**win_rate** : {metrics.get('win_rate', 0):.4f}\n"
            f"**alpha**    : {metrics.get('alpha_ratio', 0):.4f}\n"
            f"**drawdown** : {metrics.get('max_drawdown', 0):.4f}\n\n"
            f"Loop {loop} completed successfully. Starting loop {new_loop_index}.\n"
        )

        _step("win_rate     = %.4f", metrics.get("win_rate", 0))
        _step("alpha_ratio  = %.4f", metrics.get("alpha_ratio", 0))
        _step("max_drawdown = %.4f", metrics.get("max_drawdown", 0))
        _step("loop_index   : %d → %d", loop, new_loop_index)

        artifact_path = str(ARTIFACTS_DIR / f"loop_{loop}_summary.md")
        _write_artifact(artifact_path, summary)
        _result("Summary written → %s", artifact_path)

        return {
            "loop_index":   new_loop_index,
            "last_reason":  summary,
            "attempt_count": 0,
            "artifacts": state.get("artifacts", []) + [{"type": "summary", "path": artifact_path}],
        }


# ── helpers ───────────────────────────────────────────────────────────────────

def _write_artifact(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
