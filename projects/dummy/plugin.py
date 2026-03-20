"""
projects/dummy/plugin.py

DummyPlugin — for end-to-end testing of the framework.

Behaviour:
  - plan:      generates a trivial implementation_plan (fully automatic, no interrupt).
  - implement: runs immediately with no human approval required.
  - test:      returns canned metrics.
  - analyze:   FAIL on the first attempt within a loop, PASS on the second.
               TERMINATE if loop_index >= 6.
  - revise:    logs and updates last_reason.
  - summarize: generates a brief text summary, writes to ./artifacts/.

Human-in-the-loop:
  Only loop_review (every N PASS loops via notify_planka) requires human input.
  Planning-column review is handled externally via Planka + spec.md edits.
  No plan_review interrupt inside the graph.

No actual CLI agents are called — all outputs are deterministic for easy testing.
"""

import logging
import time
import os
from pathlib import Path

from framework.plugin_interface import ResearchPlugin
from framework.plugin_registry import register

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "/app/artifacts"))


@register
class DummyPlugin(ResearchPlugin):
    name = "dummy"

    # -----------------------------------------------------------------------
    # plan
    # -----------------------------------------------------------------------

    def plan_node(self, state: dict) -> dict:
        loop_index = state.get("loop_index", 0)
        decision = state.get("last_checkpoint_decision") or {}

        # Terminate if human said so during loop review
        if decision.get("action") == "terminate":
            logger.info("[DummyPlugin] plan: received terminate decision.")
            return {"last_result": "TERMINATE", "last_reason": "Terminated by human at loop review."}

        # Incorporate replan notes if present
        goal = state.get("loop_goal", "dummy research goal")
        if decision.get("action") == "replan" and decision.get("notes"):
            goal = f"{goal} [REVISED: {decision['notes']}]"
            logger.info("[DummyPlugin] plan: replanning with notes: %s", decision["notes"])

        plan = {
            "loop_index": loop_index,
            "goal": goal,
            "steps": ["step_A", "step_B", "step_C"],
        }
        logger.info("[DummyPlugin] plan: generated plan for loop %d.", loop_index)

        return {
            "loop_goal": goal,
            "implementation_plan": plan,
            "needs_human_approval": False,
            "last_checkpoint_decision": None,
        }

    # -----------------------------------------------------------------------
    # implement — runs automatically, no interrupt
    # -----------------------------------------------------------------------

    def implement_node(self, state: dict) -> dict:
        loop_index = state.get("loop_index", 0)
        logger.info("[DummyPlugin] implement: executing loop %d.", loop_index)
        time.sleep(0.3)  # simulate work

        artifact_path = str(ARTIFACTS_DIR / f"loop_{loop_index}_impl.txt")
        _write_artifact(artifact_path, f"Loop {loop_index} implementation output.\n")

        return {
            "needs_human_approval": False,
            "artifacts": state.get("artifacts", []) + [{"type": "impl", "path": artifact_path}],
        }

    # -----------------------------------------------------------------------
    # test
    # -----------------------------------------------------------------------

    def test_node(self, state: dict) -> dict:
        loop_index = state.get("loop_index", 0)
        attempt = state.get("attempt_count", 0) + 1
        logger.info("[DummyPlugin] test: loop %d attempt %d.", loop_index, attempt)
        time.sleep(0.2)
        return {
            "attempt_count": attempt,
            "test_metrics": {
                "win_rate": 0.55 + attempt * 0.05,
                "alpha_ratio": 1.2 + attempt * 0.3,
                "max_drawdown": 0.15 - attempt * 0.02,
            },
        }

    # -----------------------------------------------------------------------
    # analyze
    # -----------------------------------------------------------------------

    def analyze_node(self, state: dict) -> dict:
        loop_index = state.get("loop_index", 0)
        attempt = state.get("attempt_count", 1)
        metrics = state.get("test_metrics", {})

        if loop_index >= 6:
            logger.info("[DummyPlugin] analyze: loop_index=%d >= 6, terminating.", loop_index)
            return {"last_result": "TERMINATE", "last_reason": f"Reached max loops ({loop_index})."}

        # FAIL on first attempt, PASS on second
        if attempt < 2:
            reason = (
                f"Loop {loop_index} attempt {attempt}: "
                f"win_rate={metrics.get('win_rate', 0):.2f} below threshold."
            )
            logger.info("[DummyPlugin] analyze: FAIL — %s", reason)
            return {"last_result": "FAIL", "last_reason": reason}

        reason = (
            f"Loop {loop_index} attempt {attempt}: "
            f"win_rate={metrics.get('win_rate', 0):.2f}, "
            f"alpha_ratio={metrics.get('alpha_ratio', 0):.2f}. PASS."
        )
        logger.info("[DummyPlugin] analyze: PASS — %s", reason)
        return {"last_result": "PASS", "last_reason": reason}

    # -----------------------------------------------------------------------
    # revise
    # -----------------------------------------------------------------------

    def revise_node(self, state: dict) -> dict:
        loop_index = state.get("loop_index", 0)
        logger.info("[DummyPlugin] revise: proposing fix for loop %d.", loop_index)
        return {"last_reason": f"Loop {loop_index}: tighten entry filter and retry."}

    # -----------------------------------------------------------------------
    # summarize
    # -----------------------------------------------------------------------

    def summarize_node(self, state: dict) -> dict:
        loop_index = state.get("loop_index", 0)
        metrics = state.get("test_metrics", {})
        new_loop_index = loop_index + 1
        new_count = state.get("loop_count_since_review", 0) + 1

        summary = (
            f"## Loop {loop_index} Summary\n\n"
            f"- **Result**: PASS\n"
            f"- **Goal**: {state.get('loop_goal', '')}\n"
            f"- **win_rate**: {metrics.get('win_rate', 0):.3f}\n"
            f"- **alpha_ratio**: {metrics.get('alpha_ratio', 0):.3f}\n"
            f"- **max_drawdown**: {metrics.get('max_drawdown', 0):.3f}\n\n"
            f"Completed loop {loop_index}. Starting loop {new_loop_index}.\n"
        )

        artifact_path = str(ARTIFACTS_DIR / f"loop_{loop_index}_summary.md")
        _write_artifact(artifact_path, summary)
        logger.info("[DummyPlugin] summarize: wrote %s", artifact_path)

        return {
            "loop_index": new_loop_index,
            "loop_count_since_review": new_count,
            "last_reason": summary,
            "attempt_count": 0,
            "artifacts": state.get("artifacts", []) + [{"type": "summary", "path": artifact_path}],
        }

    def get_review_interval(self) -> int:
        return 3  # loop_review every 3 PASS loops


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _write_artifact(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
