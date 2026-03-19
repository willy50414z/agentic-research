"""
framework/api/server.py

Minimal FastAPI server (~80 lines) that replaces n8n for two jobs:
  1. /resume          — CLI or Planka webhook wakes up a paused LangGraph thread.
  2. /planka-webhook  — Receives Planka card-move events and translates to /resume.

Human decision format (sent as `decision` body):
    {"action": "continue" | "replan" | "terminate", "notes": "optional text"}

For plan-review interrupts (implement node), use:
    {"action": "approve"} or {"action": "reject", "reason": "..."}
"""

import logging
import os
import re

import httpx
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel
from langgraph.types import Command

from framework.db.queries import get_project, record_checkpoint_decision
from framework.plugin_registry import resolve as resolve_plugin
from framework.graph import get_or_build_graph

logger = logging.getLogger(__name__)

app = FastAPI(title="Agentic Research API", version="0.1.0")

DATABASE_URL = os.getenv("DATABASE_URL", "")
PLANKA_URL = os.getenv("PLANKA_API_URL", "")
PLANKA_TOKEN = os.getenv("PLANKA_TOKEN", "")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_graph(project_id: str):
    """Load project from DB, resolve plugin, return compiled graph."""
    project = get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    plugin = resolve_plugin(project["plugin_name"])
    config = {"db_url": DATABASE_URL, **project.get("config", {})}
    return get_or_build_graph(plugin, config)


def _thread_config(project_id: str) -> dict:
    return {"configurable": {"thread_id": project_id}}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class ResumeRequest(BaseModel):
    project_id: str
    decision: dict  # {"action": "continue"|"replan"|"terminate"|"approve"|"reject", "notes": "..."}


@app.post("/resume")
async def resume(body: ResumeRequest):
    """
    Resume a paused LangGraph thread with a human decision.
    Works for both plan-review (implement) and loop-review (notify_planka) interrupts.
    """
    graph = _get_graph(body.project_id)
    config = _thread_config(body.project_id)

    # Read loop_index before resuming (state.values is the pre-resume snapshot)
    pre_state = graph.get_state(config=config)
    loop_index = (pre_state.values or {}).get("loop_index", 0)

    graph.invoke(Command(resume=body.decision), config=config)

    record_checkpoint_decision(
        project_id=body.project_id,
        loop_index=loop_index,
        action=body.decision.get("action", ""),
        notes=body.decision.get("notes") or None,
        modified_plan={k: v for k, v in body.decision.items() if k not in ("action", "notes")} or None,
        db_url=DATABASE_URL,
    )

    logger.info("Resumed project '%s' with decision: %s", body.project_id, body.decision)
    return {"status": "resumed", "project_id": body.project_id}


@app.post("/planka-webhook")
async def planka_webhook(request: Request):
    """
    Receive Planka card-move webhook. Translates card position to a /resume call.

    Expected Planka webhook payload keys (simplified):
        item.id        — card ID
        list.name      — destination list name ("Approved" or "Rejected")

    The card description must contain:   thread_id: <project_id>
    """
    payload = await request.json()

    list_name = (payload.get("list") or {}).get("name", "")
    if list_name not in ("Approved", "Rejected"):
        return {"status": "ignored", "list": list_name}

    card = payload.get("item") or {}
    card_id = card.get("id")
    description = card.get("description", "")

    project_id = _extract_thread_id(description)
    if not project_id:
        logger.warning("Could not extract thread_id from card %s description.", card_id)
        return {"status": "error", "detail": "thread_id not found in card description"}

    action = "continue" if list_name == "Approved" else "terminate"
    notes = _get_latest_card_comment(card_id) if PLANKA_URL and PLANKA_TOKEN else ""

    decision = {"action": action, "notes": notes}
    graph = _get_graph(project_id)
    graph.invoke(Command(resume=decision), config=_thread_config(project_id))

    logger.info("Planka webhook: project=%s action=%s", project_id, action)
    return {"status": "ok", "project_id": project_id, "action": action}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Planka helpers
# ---------------------------------------------------------------------------

def _extract_thread_id(description: str) -> str | None:
    """Parse 'thread_id: <value>' from a Planka card description."""
    match = re.search(r"thread_id:\s*(\S+)", description)
    return match.group(1) if match else None


def _get_latest_card_comment(card_id: str) -> str:
    """Fetch the most recent comment on a Planka card (for replan notes)."""
    try:
        resp = httpx.get(
            f"{PLANKA_URL}/api/cards/{card_id}/actions",
            headers={"Authorization": f"Bearer {PLANKA_TOKEN}"},
            timeout=5,
        )
        resp.raise_for_status()
        actions = resp.json().get("items", [])
        comments = [a for a in actions if a.get("type") == "commentCard"]
        if comments:
            return comments[-1].get("data", {}).get("text", "")
    except Exception as e:
        logger.warning("Could not fetch Planka card comments: %s", e)
    return ""
