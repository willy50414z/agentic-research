"""
projects/quant_alpha/plugin.py

QuantAlphaPlugin — Phase 3 real research plugin.

Workflow per loop:
  plan      → LLM designs a momentum strategy (rsi / ma_crossover / breakout)
  implement → runs backtest on training window (bars 0..700)
  test      → runs backtest on test window (bars 700..1000)
  analyze   → LLM evaluates metrics and decides PASS / FAIL / TERMINATE
  revise    → LLM adjusts strategy parameters after FAIL
  summarize → LLM writes a markdown report, saved to artifacts/

LLM integration:
  Uses framework.llm_agent.llm_svc.run_once(LLMTarget.CLAUDE, prompt).
  Falls back to rule-based logic when Claude CLI is not installed, so the
  plugin is fully testable without an LLM.
"""

import json
import logging
import os
from pathlib import Path

import mlflow
from langgraph.types import interrupt

from framework.plugin_interface import ResearchPlugin
from framework.plugin_registry import register
from framework.llm_agent.llm_svc import run_once
from framework.llm_agent.llm_target import LLMTarget
from projects.quant_alpha.backtest import run_backtest

logger = logging.getLogger(__name__)

ARTIFACTS_DIR = Path(os.getenv("ARTIFACTS_DIR", "./artifacts"))

_MLFLOW_URI = os.getenv("MLFLOW_TRACKING_URI")
if _MLFLOW_URI:
    mlflow.set_tracking_uri(_MLFLOW_URI)


def _mlflow_log(project_id: str, loop: int, plan: dict, metrics: dict, result: str) -> None:
    """Log loop metrics to MLflow. Silently skips if MLflow is unreachable."""
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
        logger.info("[QuantAlpha] mlflow logged: project=%s loop=%d result=%s", project_id, loop, result)
    except Exception as e:
        logger.warning("[QuantAlpha] mlflow log failed (loop=%d): %s", loop, e)
_PROMPTS_DIR = Path(__file__).parent.parent.parent / "framework" / "prompts" / "quant_alpha"

_PASS_WIN_RATE      = 0.55
_PASS_ALPHA         = 1.0
_PASS_MAX_DD        = 0.20
_PASS_PROFIT_FACTOR = 1.2

_RULES_PATH = str(
    (Path(__file__).parent.parent.parent / ".ai" / "rules" / "backtest-required-metrics.md").resolve()
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_prompt(name: str) -> str:
    path = _PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")


def _call_llm(prompt: str, cwd: str | None = None) -> str:
    """
    Call Claude CLI via run_once. Returns raw stdout.
    Raises FileNotFoundError when Claude is not installed — caller must handle.
    """
    return run_once(LLMTarget.CLAUDE, prompt, timeout=120, cwd=cwd)


def _read_json_file(path: Path) -> dict:
    """Read a JSON file from disk. Returns empty dict on failure."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("Could not read JSON from '%s': %s", path, e)
        return {}


def _write_artifact(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------

@register
class QuantAlphaPlugin(ResearchPlugin):
    name = "quant_alpha"

    # ── plan ──────────────────────────────────────────────────────────────────

    def plan_node(self, state: dict) -> dict:
        loop = state.get("loop_index", 0)
        goal = state.get("loop_goal", "find alpha in momentum strategies")

        logger.info("[QuantAlpha] plan  loop=%d", loop)

        output_dir    = str(ARTIFACTS_DIR.resolve())
        strategy_dir  = str((ARTIFACTS_DIR / "strategies").resolve())
        spec          = state.get("spec") or {}
        spec_md       = spec.get("raw_md", "（spec 未提供）")

        prompt = _load_prompt("plan").format(
            SPEC          = spec_md,
            loop_index    = loop,
            last_decision = state.get("last_reason", "none"),
            STRATEGY_DIR  = strategy_dir,
            OUTPUT_DIR    = output_dir,
        )

        plan = {}
        try:
            _call_llm(prompt, cwd=output_dir)
            plan = _read_json_file(Path(output_dir) / "plan_output.json")
            logger.info("[QuantAlpha] plan  LLM strategy=%s", plan.get("strategy_name"))
        except (FileNotFoundError, RuntimeError) as e:
            logger.warning("[QuantAlpha] plan  LLM unavailable (%s) — using fallback", e)
        if not plan:
            logger.warning("[QuantAlpha] plan  file empty or unreadable — using fallback")
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

    # ── implement ─────────────────────────────────────────────────────────────

    def implement_node(self, state: dict) -> dict:
        loop = state.get("loop_index", 0)
        plan = state.get("implementation_plan", {})
        logger.info("[QuantAlpha] implement  loop=%d  strategy=%s",
                    loop, plan.get("strategy_type"))

        if state.get("needs_human_approval", False):
            logger.info("[QuantAlpha] implement  ⏸ waiting for plan review")
            decision = interrupt({
                "checkpoint": "plan_review",
                "loop_index": loop,
                "plan":       plan,
                "instruction": "Resume: {'action': 'approve'} or {'action': 'reject', 'reason': '...'}",
            })
            if isinstance(decision, dict) and decision.get("action") == "reject":
                reason = decision.get("reason", "Plan rejected.")
                logger.info("[QuantAlpha] implement  plan rejected: %s", reason)
                return {"last_result": "TERMINATE", "last_reason": reason,
                        "needs_human_approval": False}

        # Run backtest on training window (first 700 bars)
        train_result = run_backtest(plan, n_bars=700)
        logger.info("[QuantAlpha] implement  train win_rate=%.4f  n_trades=%d",
                    train_result["win_rate"], train_result["n_trades"])

        artifact_path = str(ARTIFACTS_DIR / f"loop_{loop}_train.json")
        _write_artifact(artifact_path, json.dumps(
            {"loop": loop, "plan": plan, "train_result": train_result}, indent=2))

        return {
            "needs_human_approval": False,
            "artifacts": state.get("artifacts", []) + [
                {"type": "train_result", "path": artifact_path}
            ],
        }

    # ── test ──────────────────────────────────────────────────────────────────

    def test_node(self, state: dict) -> dict:
        loop    = state.get("loop_index", 0)
        attempt = state.get("attempt_count", 0) + 1
        plan    = state.get("implementation_plan", {})
        logger.info("[QuantAlpha] test  loop=%d  attempt=%d  strategy=%s",
                    loop, attempt, plan.get("strategy_type"))

        # Run backtest on test window (last 300 bars, offset by attempt for variety)
        result = run_backtest(plan, n_bars=300 + attempt * 50)
        logger.info("[QuantAlpha] test  win_rate=%.4f  alpha=%.4f  drawdown=%.4f",
                    result["win_rate"], result["alpha_ratio"], result["max_drawdown"])

        return {
            "attempt_count": attempt,
            "test_metrics": {
                "win_rate":      result["win_rate"],
                "alpha_ratio":   result["alpha_ratio"],
                "max_drawdown":  result["max_drawdown"],
                "n_trades":      result["n_trades"],
                "total_return":  result["total_return"],
                "profit_factor": result.get("profit_factor", 0.0),
            },
        }

    # ── analyze ───────────────────────────────────────────────────────────────

    def analyze_node(self, state: dict) -> dict:
        loop    = state.get("loop_index", 0)
        plan    = state.get("implementation_plan", {})
        metrics = state.get("test_metrics", {})
        logger.info("[QuantAlpha] analyze  loop=%d", loop)

        # Propagate TERMINATE set by implement on reject
        if state.get("last_result") == "TERMINATE":
            return {"last_result": "TERMINATE", "last_reason": state.get("last_reason", "")}

        output_dir      = str(ARTIFACTS_DIR.resolve())
        target_pf       = (
            (state.get("spec") or {}).get("performance", {}).get("is_profit_factor")
            or _PASS_PROFIT_FACTOR
        )
        prompt = _load_prompt("analyze").format(
            strategy_name        = plan.get("strategy_name", "?"),
            params               = json.dumps({k: v for k, v in plan.items()
                                               if k != "target_win_rate"}),
            win_rate             = metrics.get("win_rate", 0),
            alpha_ratio          = metrics.get("alpha_ratio", 0),
            max_drawdown         = metrics.get("max_drawdown", 0),
            profit_factor        = metrics.get("profit_factor", 0.0),
            n_trades             = metrics.get("n_trades", 0),
            target_win_rate      = plan.get("target_win_rate", _PASS_WIN_RATE),
            target_profit_factor = target_pf,
            loop_index           = loop,
            RULES_PATH           = _RULES_PATH,
            OUTPUT_DIR           = output_dir,
        )

        try:
            _call_llm(prompt, cwd=output_dir)
            result_file = Path(output_dir) / "analyze_result.txt"
            lines  = result_file.read_text(encoding="utf-8").strip().splitlines()
            result = (lines[0].strip().upper() if lines else "FAIL")
            reason = (lines[1].strip() if len(lines) > 1 else "")
            logger.info("[QuantAlpha] analyze  LLM result=%s", result)
        except (FileNotFoundError, RuntimeError) as e:
            logger.warning("[QuantAlpha] analyze  LLM unavailable (%s) — rule-based fallback", e)
            result, reason = self._rule_based_analyze(loop, plan, metrics)
        except Exception as e:
            logger.warning("[QuantAlpha] analyze  file read failed (%s) — rule-based fallback", e)
            result, reason = self._rule_based_analyze(loop, plan, metrics)

        if result not in ("PASS", "FAIL", "TERMINATE"):
            result = "FAIL"

        if result == "PASS":
            logger.info("[QuantAlpha] analyze  ✔ PASS — %s", reason)
        elif result == "TERMINATE":
            logger.info("[QuantAlpha] analyze  ✘ TERMINATE — %s", reason)
        else:
            logger.info("[QuantAlpha] analyze  ✘ FAIL — %s → will revise params", reason)

        _mlflow_log(
            project_id=state.get("project_id", "unknown"),
            loop=loop,
            plan=plan,
            metrics=metrics,
            result=result,
        )

        return {"last_result": result, "last_reason": reason}

    def _rule_based_analyze(self, loop, plan, metrics):
        win_rate      = metrics.get("win_rate", 0)
        alpha_ratio   = metrics.get("alpha_ratio", 0)
        max_dd        = metrics.get("max_drawdown", 1)
        profit_factor = metrics.get("profit_factor", 0.0)
        target_wr     = plan.get("target_win_rate", _PASS_WIN_RATE)

        if (win_rate >= target_wr and alpha_ratio >= _PASS_ALPHA
                and max_dd <= _PASS_MAX_DD and profit_factor >= _PASS_PROFIT_FACTOR):
            return "PASS", (
                f"win_rate={win_rate:.4f} ≥ {target_wr}  "
                f"alpha={alpha_ratio:.4f} ≥ 1.0  "
                f"drawdown={max_dd:.4f} ≤ 0.20  "
                f"profit_factor={profit_factor:.4f} ≥ {_PASS_PROFIT_FACTOR}"
            )
        fails = []
        if win_rate      < target_wr:           fails.append(f"win_rate={win_rate:.4f} < {target_wr}")
        if alpha_ratio   < _PASS_ALPHA:         fails.append(f"alpha={alpha_ratio:.4f} < 1.0")
        if max_dd        > _PASS_MAX_DD:        fails.append(f"drawdown={max_dd:.4f} > 0.20")
        if profit_factor < _PASS_PROFIT_FACTOR: fails.append(f"profit_factor={profit_factor:.4f} < {_PASS_PROFIT_FACTOR}")
        return "FAIL", "Failed: " + "; ".join(fails)

    # ── revise ────────────────────────────────────────────────────────────────

    def revise_node(self, state: dict) -> dict:
        loop    = state.get("loop_index", 0)
        plan    = dict(state.get("implementation_plan") or {})
        reason  = state.get("last_reason", "")
        attempt = state.get("attempt_count", 1)
        logger.info("[QuantAlpha] revise  loop=%d  attempt=%d", loop, attempt)

        if attempt >= 3:
            return {"last_result": "TERMINATE",
                    "last_reason": f"Max revision attempts ({attempt}) reached."}

        output_dir = str(ARTIFACTS_DIR.resolve())
        prompt = _load_prompt("revise").format(
            params        = json.dumps(plan, ensure_ascii=False, indent=2),
            reason        = reason,
            attempt_count = attempt,
            OUTPUT_DIR    = output_dir,
        )

        revised = {}
        try:
            _call_llm(prompt, cwd=output_dir)
            result_file = Path(output_dir) / "revise_result.txt"
            lines  = result_file.read_text(encoding="utf-8").strip().splitlines()
            result = (lines[0].strip().upper() if lines else "TERMINATE")
            reason = (lines[1].strip() if len(lines) > 1 else reason)
            if result == "TERMINATE":
                return {"last_result": "TERMINATE", "last_reason": reason}
            revised = _read_json_file(Path(output_dir) / "revised_params.json")
            logger.info("[QuantAlpha] revise  LLM revised strategy=%s",
                        revised.get("strategy_type"))
        except (FileNotFoundError, RuntimeError) as e:
            logger.warning("[QuantAlpha] revise  LLM unavailable (%s) — rule-based fallback", e)
        except Exception as e:
            logger.warning("[QuantAlpha] revise  file read failed (%s) — rule-based fallback", e)

        if not revised:
            # Rule-based fallback: tighten stoploss slightly
            revised = dict(plan)
            revised["stoploss"] = round(max(-0.02, plan.get("stoploss", -0.05) + 0.01), 3)
            reason = f"Tightened stoploss to {revised['stoploss']} (rule-based fallback)."

        logger.info(
            "[QuantAlpha] revise  ↻ strategy=%s stoploss %s→%s",
            revised.get("strategy_name"), plan.get("stoploss"), revised.get("stoploss"),
        )

        return {
            "implementation_plan":  revised,
            "last_reason":          reason,
            "needs_human_approval": False,
        }

    # ── summarize ─────────────────────────────────────────────────────────────

    def summarize_node(self, state: dict) -> dict:
        loop    = state.get("loop_index", 0)
        plan    = state.get("implementation_plan", {})
        metrics = state.get("test_metrics", {})
        goal    = state.get("loop_goal", "")
        logger.info("[QuantAlpha] summarize  loop=%d", loop)

        new_loop_index = loop + 1

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
            # Extract first line summary (_摘要：..._) if present
            first_line = report_md.splitlines()[0] if report_md else ""
            summary = first_line.strip("_").replace("摘要：", "").strip() if "摘要" in first_line else ""
            logger.info("[QuantAlpha] summarize  LLM report generated (%d chars)", len(report_md))
        except (FileNotFoundError, RuntimeError) as e:
            logger.warning("[QuantAlpha] summarize  LLM unavailable (%s) — generating report", e)
        except Exception as e:
            logger.warning("[QuantAlpha] summarize  file read failed (%s) — generating report", e)

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
                f"| total_return | {metrics.get('total_return', 0):.4f} |\n\n"
                f"## Next Steps\n\nContinue with next loop strategy iteration.\n"
            )
            summary = (
                f"Loop {loop} PASS: win_rate={metrics.get('win_rate',0):.4f} "
                f"alpha={metrics.get('alpha_ratio',0):.4f}"
            )

        artifact_path = str(ARTIFACTS_DIR / f"loop_{loop}_report.md")
        _write_artifact(artifact_path, report_md)
        logger.info("[QuantAlpha] summarize  report → %s", artifact_path)

        return {
            "loop_index":   new_loop_index,
            "last_reason":  summary,
            "attempt_count": 0,
            "artifacts": state.get("artifacts", []) + [
                {"type": "summary", "path": artifact_path}
            ],
        }

    # ── terminate_summarize ───────────────────────────────────────────────────

    def terminate_summarize_node(self, state: dict) -> dict:
        loop    = state.get("loop_index", 0)
        plan    = state.get("implementation_plan") or {}
        metrics = state.get("test_metrics") or {}
        goal    = state.get("loop_goal", "")
        reason  = state.get("last_reason", "Max attempts reached.")
        attempt = state.get("attempt_count", 0)
        logger.info("[QuantAlpha] terminate_summarize  loop=%d  attempts=%d", loop, attempt)

        # Build attempts table from artifacts in state
        artifacts = list(state.get("artifacts") or [])
        attempts_lines = []
        for a in artifacts:
            if a.get("type") == "train_result":
                attempts_lines.append(f"  - {a['path']}")

        # Last test metrics row
        if metrics:
            attempts_lines.append(
                f"  - final: win_rate={metrics.get('win_rate',0):.4f} "
                f"alpha={metrics.get('alpha_ratio',0):.4f} "
                f"drawdown={metrics.get('max_drawdown',0):.4f} "
                f"trades={metrics.get('n_trades',0)}"
            )
        attempts_table = "\n".join(attempts_lines) if attempts_lines else "  (no attempts recorded)"

        output_dir = str(ARTIFACTS_DIR.resolve())
        prompt = _load_prompt("terminate_summary").format(
            project_id      = state.get("project_id", "?"),
            goal            = goal,
            strategy_name   = plan.get("strategy_name", "?"),
            terminate_reason= reason,
            attempt_count   = attempt,
            attempts_table  = attempts_table,
            target_win_rate = plan.get("target_win_rate", _PASS_WIN_RATE),
            OUTPUT_DIR      = output_dir,
        )

        report_md = ""
        summary   = ""
        try:
            _call_llm(prompt, cwd=output_dir)
            report_file = Path(output_dir) / "termination_report.md"
            report_md = report_file.read_text(encoding="utf-8").strip()
            # Extract summary from _摘要：..._ line if present
            for line in report_md.splitlines():
                if "摘要" in line:
                    summary = line.strip("_").replace("摘要：", "").strip()
                    break
            logger.info("[QuantAlpha] terminate_summarize  LLM report generated (%d chars)", len(report_md))
        except (FileNotFoundError, RuntimeError) as e:
            logger.warning("[QuantAlpha] terminate_summarize  LLM unavailable (%s) — using default", e)
        except Exception as e:
            logger.warning("[QuantAlpha] terminate_summarize  file read failed (%s) — using default", e)

        if not report_md:
            # Fall back to base-class template
            result = super().terminate_summarize_node(state)
            return result

        artifact_path = str(ARTIFACTS_DIR / f"loop_{loop}_terminate_report.md")
        _write_artifact(artifact_path, report_md)
        logger.info("[QuantAlpha] terminate_summarize  report → %s", artifact_path)

        return {
            "last_reason": summary or f"Loop {loop} TERMINATE: {reason}",
            "artifacts": artifacts + [{"type": "terminate_summary", "path": artifact_path}],
        }

