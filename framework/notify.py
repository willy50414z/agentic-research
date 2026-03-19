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
import httpx
from langgraph.types import interrupt

logger = logging.getLogger(__name__)

PLANKA_URL = os.getenv("PLANKA_API_URL", "")
PLANKA_TOKEN = os.getenv("PLANKA_TOKEN", "")


def notify_planka_node(state: dict) -> dict:
    """
    LangGraph node that:
      1. Optionally creates a Planka review card.
      2. Calls interrupt() to pause and wait for human loop-review decision.
      3. Returns state update with decision and reset loop counter.

    interrupt() value expected:
        {"action": "continue" | "replan" | "terminate", "notes": "optional text"}
    """
    project_id = state.get("project_id", "unknown")
    loop_index = state.get("loop_index", 0)
    summary = state.get("last_reason", "No summary available.")

    # --- Optional: create Planka card ---
    if PLANKA_URL and PLANKA_TOKEN:
        _create_planka_card(project_id, loop_index, summary)
    else:
        logger.info(
            "[notify_planka] Planka not configured — skipping card creation. "
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


def _create_planka_card(project_id: str, loop_index: int, summary: str) -> None:
    """Create a review card in Planka. Non-blocking — logs warning on failure."""
    try:
        # Planka REST API: POST /api/cards
        # Requires board_id / list_id — read from env or project config.
        board_list_id = os.getenv("PLANKA_REVIEW_LIST_ID", "")
        if not board_list_id:
            logger.warning("[notify_planka] PLANKA_REVIEW_LIST_ID not set, skipping card.")
            return

        card_data = {
            "boardListId": board_list_id,
            "name": f"[{project_id}] Loop {loop_index} Review",
            "description": (
                f"**Project:** {project_id}\n"
                f"**Loop:** {loop_index}\n\n"
                f"**Summary:**\n{summary}\n\n"
                f"**thread_id:** {project_id}\n\n"
                f"Move this card to **Approved** or **Rejected**, "
                f"then run:\n"
                f"  `python cli/main.py approve --project {project_id} --action continue`"
            ),
        }
        resp = httpx.post(
            f"{PLANKA_URL}/api/cards",
            headers={"Authorization": f"Bearer {PLANKA_TOKEN}"},
            json=card_data,
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("[notify_planka] Planka card created: %s", resp.json().get("item", {}).get("id"))
    except Exception as e:
        logger.warning("[notify_planka] Planka card creation failed (non-blocking): %s", e)
