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

    def get_review_interval(self) -> int:
        """Number of PASS loops between human review checkpoints. Default: 5."""
        return 5
