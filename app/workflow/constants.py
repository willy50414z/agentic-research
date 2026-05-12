"""
app/workflow/constants.py

Shared enums, protocols, and type aliases for the step-based workflow.
"""

from collections.abc import Callable
from enum import Enum
from typing import Protocol


class WorkflowStep(str, Enum):
    PLAN       = "plan"
    IMPLEMENT  = "implement"
    TEST       = "test"
    ANALYZE    = "analyze"
    REVISE     = "revise"      # FAIL path: 2-LLM plan revision before next implement
    SUMMARIZE  = "summarize"
    TERMINATE  = "terminate"   # terminal: exhausted or LLM-requested stop
    DONE       = "done"        # terminal: successful completion


class AnalysisResult(str, Enum):
    PASS      = "PASS"
    FAIL      = "FAIL"
    TERMINATE = "TERMINATE"
    UNKNOWN   = "UNKNOWN"


class PlankaColumn(str, Enum):
    PLANNING     = "Planning"
    SPEC_PENDING = "Spec Pending Review"
    EXECUTING    = "Executing"
    REVIEW       = "Review"
    DONE         = "Done"
    FAILED       = "Failed"


class WorkflowSink(Protocol):
    def post_comment(self, project_id: str, text: str) -> None: ...
    def resolve_card_id(self, project_id: str) -> str | None: ...
    def upload_spec_attachment(self, card_id: str, filename: str, content: str) -> None: ...


# Callable shape expected for move_card_fn: (project_id, column_name) -> None
MoveCardFn = Callable[[str, str], None] | None
