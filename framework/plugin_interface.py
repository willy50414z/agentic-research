"""
framework/plugin_interface.py

Abstract base class for research workflow plugins.
Each plugin implements LangGraph node functions for a specific research domain.

Node functions receive a state dict and return a state-update dict.
Routing is driven by state["last_result"]: "PASS" | "FAIL" | "TERMINATE".
"""

from abc import ABC, abstractmethod


class ResearchPlugin(ABC):
    """
    Base class for all research plugins.

    Implementors fill in domain-specific logic (prompts, backtests, CLI calls).
    The framework wires nodes into the LangGraph graph and handles persistence,
    interrupts, and Planka notifications.

    State keys used by the framework (do not clobber):
        project_id, loop_index, loop_goal, last_result, last_reason,
        loop_count_since_review, last_checkpoint_decision,
        needs_human_approval, artifacts
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plugin identifier, e.g. 'quant_alpha' or 'dummy'."""

    # ------------------------------------------------------------------
    # Required node implementations
    # ------------------------------------------------------------------

    @abstractmethod
    def plan_node(self, state: dict) -> dict:
        """
        Generate an implementation plan for the current loop.

        Reads:  loop_goal, last_checkpoint_decision (for replan notes)
        Writes: implementation_plan (dict), needs_human_approval=True

        If last_checkpoint_decision.action == "terminate", set last_result="TERMINATE".
        """

    @abstractmethod
    def implement_node(self, state: dict) -> dict:
        """
        Execute the implementation (write code, upload artifacts, etc.).

        This node calls interrupt() when needs_human_approval=True to pause
        for human plan review. The framework provides a default stub; plugins
        should call super().implement_node(state) or replicate the interrupt logic.

        Writes: needs_human_approval=False, artifacts (appended)
        """

    @abstractmethod
    def test_node(self, state: dict) -> dict:
        """
        Run tests / backtests / validation.

        Writes: any domain-specific metrics into state (plugin-defined keys).
        """

    @abstractmethod
    def analyze_node(self, state: dict) -> dict:
        """
        Analyze test results and set the routing signal.

        Writes:
            last_result: "PASS" | "FAIL" | "TERMINATE"
            last_reason: human-readable explanation
        """

    @abstractmethod
    def revise_node(self, state: dict) -> dict:
        """
        Propose a fix after FAIL. Update loop_goal or implementation details.

        Writes: loop_goal (revised), implementation_plan (optional reset)
        """

    @abstractmethod
    def summarize_node(self, state: dict) -> dict:
        """
        Summarize N-loop progress into a report.

        Writes:
            last_reason: summary text (shown in Planka card and CLI status)
            artifacts: append report ref (local path or MinIO key)
        """

    # ------------------------------------------------------------------
    # Optional overrides
    # ------------------------------------------------------------------

    def terminate_summarize_node(self, state: dict) -> dict:
        """
        Generate a summary report when the research terminates without PASS.

        Called just before END on the TERMINATE path.
        Default implementation writes a plain-text markdown summary from state.
        Override in plugins to add LLM-generated analysis.

        Writes:
            last_reason: summary text
            artifacts:   append report ref
        """
        import json
        import os
        from pathlib import Path

        loop      = state.get("loop_index", 0)
        plan      = state.get("implementation_plan") or {}
        metrics   = state.get("test_metrics") or {}
        artifacts = list(state.get("artifacts") or [])
        goal      = state.get("loop_goal", "")
        reason    = state.get("last_reason", "Max attempts reached.")

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

        artifacts_dir = Path(os.getenv("ARTIFACTS_DIR", "./artifacts"))
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        report_path = str(artifacts_dir / f"loop_{loop}_terminate_report.md")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report_md)

        return {
            "last_reason": summary,
            "artifacts": artifacts + [{"type": "terminate_summary", "path": report_path}],
        }

    def get_review_interval(self) -> int:
        """Number of PASS loops between human review checkpoints. Default: 5."""
        return 5
