"""
framework/notify.py

LangGraph node: notify_planka_node

Creates a Planka review card with the loop summary, then calls interrupt()
to pause the graph and wait for a human decision.

The human (via CLI or Planka webhook) resumes with:
    Command(resume={"action": "continue" | "replan" | "terminate", "notes": "..."})

If PLANKA_API_URL is not set, Planka notification is skipped (log only).
This keeps Planka fully optional — Phase 1-2 can run without it.
"""

import os
import logging
from langgraph.types import interrupt

logger = logging.getLogger(__name__)

PLANKA_URL = os.getenv("PLANKA_API_URL", "")
PLANKA_TOKEN = os.getenv("PLANKA_TOKEN", "")


def notify_planka_node(state: dict) -> dict:
    """
    LangGraph node that:
      1. Posts a review-checkpoint comment to the project's Planka card.
      2. Calls interrupt() to pause and wait for human loop-review decision.
      3. Returns state update with decision and reset loop counter.

    interrupt() value expected:
        {"action": "continue" | "replan" | "terminate", "notes": "optional text"}
    """
    project_id = state.get("project_id", "unknown")
    loop_index = state.get("loop_index", 0)
    summary = state.get("last_reason", "No summary available.")

    # Post checkpoint comment to the main project card
    if PLANKA_URL and PLANKA_TOKEN:
        _post_checkpoint_comment(project_id, loop_index, summary)
    else:
        logger.info(
            "[notify_planka] Planka not configured — skipping comment. "
            "Set PLANKA_API_URL + PLANKA_TOKEN to enable."
        )

    logger.info(
        "[notify_planka] Loop %d review checkpoint for project '%s'. "
        "Waiting for human decision (continue / replan / terminate).",
        loop_index, project_id,
    )

    # --- Interrupt: wait for human decision ---
    decision = interrupt({
        "checkpoint": "loop_review",
        "project_id": project_id,
        "loop_index": loop_index,
        "summary": summary,
        "instruction": "Resume with: {'action': 'continue'|'replan'|'terminate', 'notes': '...'}",
    })

    logger.info("[notify_planka] Resumed with decision: %s", decision)

    return {
        "last_checkpoint_decision": decision,
        "loop_count_since_review": 0,  # reset counter after review
    }


def _post_checkpoint_comment(project_id: str, loop_index: int, summary: str) -> None:
    """Post a review-checkpoint comment on the main project card. Non-blocking."""
    try:
        from framework.planka import PlankaSink
        sink = PlankaSink(
            PLANKA_URL,
            PLANKA_TOKEN,
            os.getenv("PLANKA_BOARD_ID", ""),
            os.getenv("DATABASE_URL", ""),
        )
        text = (
            f"[REVIEW CHECKPOINT] Loop {loop_index}\n\n"
            f"{summary[:500]}\n\n"
            f"Awaiting human decision: **continue** / **replan** / **terminate**"
        )
        sink.post_comment(project_id, text)
        logger.info("[notify_planka] Checkpoint comment posted for project '%s' loop %d.", project_id, loop_index)
    except Exception as e:
        logger.warning("[notify_planka] Checkpoint comment failed (non-blocking): %s", e)
