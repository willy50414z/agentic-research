"""
projects/sample/plugin.py

SamplePlugin — Hyperparameter Search Simulation

A beginner-friendly demo plugin that shows the full research loop:
  plan → implement (Plan Review ⏸) → test → analyze
           │ FAIL → revise → implement (no interrupt) → test → analyze → PASS
           │ PASS → summarize → record_metrics
           │                        │ every 2 loops → Loop Review ⏸
           │                        │ continue/replan → next loop
           │                        │ terminate → END

Simulated experiment:
  - Loop 0: lr=0.01  batch=32  → acc=0.61 → FAIL → revise → lr=0.001 batch=64 → acc=0.86 → PASS
  - Loop 1: lr=0.001 batch=128 → acc=0.77 → PASS  ← Loop Review fires here
  - Loop 2: lr=0.0003 batch=64 → acc=0.91 → PASS  ← Loop Review fires again

Human interaction points:
  1. Plan Review after each plan_node (--action approve / reject)
  2. Loop Review every 2 PASS loops  (--action continue / replan / terminate)

MLflow logging: set MLFLOW_TRACKING_URI env var (e.g. http://localhost:5000).
If unset, MLflow calls are silently skipped.
"""

import hashlib
import logging
import os
from pathlib import Path

from framework.plugin_interface import ResearchPlugin
from framework.plugin_registry import register

logger = logging.getLogger("sample.plugin")

ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "./artifacts"))

# ---------------------------------------------------------------------------
# Config space — one config per loop slot; revise shifts to next entry
# ---------------------------------------------------------------------------
_CONFIGS = [
    {"lr": 0.01,   "batch_size": 32,  "epochs": 10},   # Loop 0 initial → FAIL (acc=0.61)
    {"lr": 0.001,  "batch_size": 64,  "epochs": 20},   # Loop 0 revised → PASS (acc=0.86)
    {"lr": 0.001,  "batch_size": 128, "epochs": 30},   # Loop 1         → PASS (acc=0.77)
    {"lr": 0.0003, "batch_size": 64,  "epochs": 20},   # Loop 2+        → PASS (acc=0.91)
]

_ACCURACY_THRESHOLD = 0.75


def _simulate_accuracy(config: dict) -> float:
    """Deterministic accuracy based on config — same params always give same result."""
    key = f"{config['lr']}-{config['batch_size']}-{config['epochs']}"
    seed = int(hashlib.md5(key.encode()).hexdigest(), 16) % 10000
    return round(0.60 + (seed / 10000) * 0.35, 4)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

@register
class SamplePlugin(ResearchPlugin):
    """Demo hyperparameter search plugin for the Agentic Research Workflow Engine."""

    name = "sample"

    # --- plan -----------------------------------------------------------

    def plan_node(self, state: dict) -> dict:
        loop = state.get("loop_index", 0)
        goal = state.get("loop_goal", "find best model config")

        config = _CONFIGS[min(loop, len(_CONFIGS) - 1)]
        plan   = {"loop": loop, "config": config, "goal": goal}

        logger.info("[Sample] plan    loop=%d  config=lr=%s batch=%s epochs=%s",
                    loop, config["lr"], config["batch_size"], config["epochs"])

        return {
            "loop_goal":            goal,
            "implementation_plan":  plan,
            "needs_human_approval": True,
        }

    # --- implement -------------------------------------------------------

    def implement_node(self, state: dict) -> dict:
        from langgraph.types import interrupt

        plan    = state.get("implementation_plan", {})
        loop    = plan.get("loop", 0)
        attempt = state.get("attempt_count", 0)

        if state.get("needs_human_approval", False):
            logger.info("[Sample] implement  loop=%d attempt=%d — ⏸ Plan Review",
                        loop, attempt + 1)
            decision = interrupt({
                "checkpoint":  "plan_review",
                "loop_index":  loop,
                "plan":        plan,
                "instruction": (
                    "Approve or reject this plan.\n"
                    "  approve : python cli/main.py approve --project <id> --action approve\n"
                    "  reject  : python cli/main.py approve --project <id> --action reject --reason \"...\""
                ),
            })

            if isinstance(decision, dict) and decision.get("action") == "reject":
                reason = decision.get("reason", "Plan rejected.")
                logger.info("[Sample] implement: plan rejected — %s", reason)
                return {
                    "last_result":          "TERMINATE",
                    "last_reason":          f"Plan rejected: {reason}",
                    "needs_human_approval": False,
                }

        logger.info("[Sample] implement  loop=%d attempt=%d  running experiment...",
                    loop, attempt + 1)
        return {"needs_human_approval": False}

    # --- test ------------------------------------------------------------

    def test_node(self, state: dict) -> dict:
        plan    = state.get("implementation_plan", {})
        config  = plan.get("config", {})
        loop    = plan.get("loop", 0)
        attempt = state.get("attempt_count", 0)

        accuracy = _simulate_accuracy(config)

        logger.info("[Sample] test    loop=%d attempt=%d  lr=%s batch=%s epochs=%s  accuracy=%.4f",
                    loop, attempt + 1,
                    config.get("lr"), config.get("batch_size"), config.get("epochs"),
                    accuracy)

        _try_mlflow_log(
            project_id=state.get("project_id", ""),
            loop=loop,
            attempt=attempt,
            params={k: v for k, v in config.items()},
            metrics={"accuracy": accuracy},
        )

        return {
            "test_metrics": {
                "accuracy":   accuracy,
                "lr":         config.get("lr"),
                "batch_size": config.get("batch_size"),
                "epochs":     config.get("epochs"),
            },
            "attempt_count": attempt + 1,
        }

    # --- analyze ---------------------------------------------------------

    def analyze_node(self, state: dict) -> dict:
        metrics  = state.get("test_metrics", {})
        accuracy = metrics.get("accuracy", 0.0)

        if accuracy >= _ACCURACY_THRESHOLD:
            result = "PASS"
            reason = (
                f"accuracy={accuracy:.4f} ≥ {_ACCURACY_THRESHOLD}  "
                f"lr={metrics.get('lr')}  batch={metrics.get('batch_size')}"
            )
            logger.info("[Sample] analyze  ✔ PASS — %s", reason)
        else:
            result = "FAIL"
            reason = (
                f"accuracy={accuracy:.4f} < {_ACCURACY_THRESHOLD}  "
                f"→ will revise config"
            )
            logger.info("[Sample] analyze  ✘ FAIL — %s", reason)

        return {"last_result": result, "last_reason": reason}

    # --- revise ----------------------------------------------------------

    def revise_node(self, state: dict) -> dict:
        plan       = state.get("implementation_plan", {})
        attempt    = state.get("attempt_count", 0)
        old_config = plan.get("config", {})
        new_config = _CONFIGS[min(attempt, len(_CONFIGS) - 1)]

        logger.info("[Sample] revise  ↻ config changed: lr %s→%s  batch %s→%s",
                    old_config.get("lr"), new_config["lr"],
                    old_config.get("batch_size"), new_config["batch_size"])

        plan["config"] = new_config
        return {"implementation_plan": plan}

    # --- summarize -------------------------------------------------------

    def summarize_node(self, state: dict) -> dict:
        loop    = state.get("loop_index", 0)
        metrics = state.get("test_metrics", {})
        goal    = state.get("loop_goal", "")

        summary = (
            f"Loop {loop} PASS — "
            f"accuracy={metrics.get('accuracy', 0):.4f}  "
            f"lr={metrics.get('lr')}  "
            f"batch={metrics.get('batch_size')}"
        )
        logger.info("[Sample] summarize  loop=%d  %s", loop, summary)

        # Write markdown report
        ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
        report_path = ARTIFACTS_DIR / f"sample_loop_{loop}.md"
        report_path.write_text(
            f"# Sample Research — Loop {loop} Report\n\n"
            f"**Goal:** {goal}\n\n"
            f"**Result:** PASS\n\n"
            f"## Metrics\n\n"
            f"| Metric | Value |\n"
            f"|--------|-------|\n"
            f"| accuracy | {metrics.get('accuracy', 0):.4f} |\n"
            f"| lr | {metrics.get('lr')} |\n"
            f"| batch_size | {metrics.get('batch_size')} |\n"
            f"| epochs | {metrics.get('epochs')} |\n"
        )

        return {
            "last_reason":  summary,
            "loop_index":   loop + 1,
            "attempt_count": 0,
            "artifacts": state.get("artifacts", []) + [
                {"type": "summary", "path": str(report_path)}
            ],
        }


# ---------------------------------------------------------------------------
# MLflow helper (optional)
# ---------------------------------------------------------------------------

def _try_mlflow_log(project_id: str, loop: int, attempt: int,
                    params: dict, metrics: dict) -> None:
    """Log to MLflow if MLFLOW_TRACKING_URI is set. Silently skips if not."""
    tracking_uri = os.getenv("MLFLOW_TRACKING_URI", "")
    if not tracking_uri:
        return
    try:
        import mlflow
        mlflow.set_tracking_uri(tracking_uri)
        mlflow.set_experiment(project_id or "sample")
        run_name = f"loop_{loop}_attempt_{attempt}"
        with mlflow.start_run(run_name=run_name):
            mlflow.log_params(params)
            for k, v in metrics.items():
                mlflow.log_metric(k, v)
        logger.debug("[Sample] MLflow run logged: %s", run_name)
    except Exception as e:
        logger.debug("[Sample] MLflow log skipped: %s", e)
