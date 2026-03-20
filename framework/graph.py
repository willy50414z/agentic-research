"""
framework/graph.py

Core LangGraph graph builder for the agentic research workflow.

Graph flow (fully automatic except for loop_review):
    START → plan → implement → test → analyze
                                          │ FAIL → revise → implement
                                          │ PASS → summarize → record_metrics
                                          │           │ every N loops → notify_planka [INTERRUPT]
                                          │           │ continue/replan → plan
                                          │ TERMINATE → record_terminate_metrics → END

Human-in-the-loop:
    Only notify_planka calls interrupt() to wait for the loop-review decision.
    Planning-column review (Phase 1 spec clarification) is handled externally
    via Planka card position + spec.md edits — not via LangGraph interrupt.

Resume pattern (loop review only):
    graph.invoke(Command(resume={"action": "continue"|"replan"|"terminate", "notes": "..."}), config)
"""

import logging
import os
from typing import TypedDict, Optional

import psycopg
from langgraph.graph import StateGraph, END, START
from langgraph.checkpoint.postgres import PostgresSaver

from .plugin_interface import ResearchPlugin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# State schema
# ---------------------------------------------------------------------------

class ResearchState(TypedDict):
    project_id: str
    loop_index: int
    loop_goal: str
    spec: Optional[dict]                # full structured spec injected at start time
    implementation_plan: Optional[dict]
    last_result: str                    # PASS | FAIL | TERMINATE | UNKNOWN
    last_reason: str
    loop_count_since_review: int
    last_checkpoint_decision: Optional[dict]
    needs_human_approval: bool
    attempt_count: int                  # tracks revise→implement attempts within one loop
    test_metrics: dict                  # latest test result metrics (plugin-defined keys)
    artifacts: list                     # lightweight refs (local paths or MinIO keys)


# ---------------------------------------------------------------------------
# Routing functions
# ---------------------------------------------------------------------------

def _analyze_router(state: ResearchState) -> str:
    result = state.get("last_result", "UNKNOWN")
    if result == "PASS":
        return "pass"
    if result == "TERMINATE":
        return "terminate"
    return "fail"


def _after_review_router(state: ResearchState) -> str:
    decision = state.get("last_checkpoint_decision") or {}
    action = decision.get("action", "continue")
    if action == "terminate":
        return "terminate"
    if action == "replan":
        return "replan"
    return "continue"


def _make_loop_counter_router(review_interval: int):
    def router(state: ResearchState) -> str:
        if state.get("loop_count_since_review", 0) >= review_interval:
            return "checkpoint"
        return "continue"
    return router


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def _make_record_metrics_node(db_url: str):
    """
    Framework node: writes loop_metrics after every PASS.
    Runs after summarize_node, which already incremented loop_index,
    so completed loop = loop_index - 1.
    """
    from .db.queries import record_loop_metrics

    def record_metrics_node(state: dict) -> dict:
        completed_loop = state.get("loop_index", 1) - 1
        try:
            record_loop_metrics(
                project_id=state.get("project_id", ""),
                loop_index=completed_loop,
                result=state.get("last_result", "PASS"),
                reason=state.get("last_reason", ""),
                report_path=next(
                    (a["path"] for a in reversed(state.get("artifacts", []))
                     if a.get("type") == "summary"),
                    None,
                ),
                metrics=state.get("test_metrics", {}),
                db_url=db_url,
            )
        except Exception as e:
            logger.warning("loop_metrics write failed (non-blocking): %s", e)
        return {}

    return record_metrics_node


def _make_record_terminate_metrics_node(db_url: str):
    """
    Framework node: writes loop_metrics when a loop terminates without PASS.
    Runs on the TERMINATE path from analyze (before END), preserving the
    current loop_index (summarize never ran, so it was not incremented).
    """
    from .db.queries import record_loop_metrics

    def record_terminate_metrics_node(state: dict) -> dict:
        loop = state.get("loop_index", 0)
        try:
            record_loop_metrics(
                project_id=state.get("project_id", ""),
                loop_index=loop,
                result=state.get("last_result", "TERMINATE"),
                reason=state.get("last_reason", ""),
                report_path=None,
                metrics=state.get("test_metrics", {}),
                db_url=db_url,
            )
            logger.info("terminate_metrics recorded: project=%s loop=%d", state.get("project_id"), loop)
        except Exception as e:
            logger.warning("terminate_metrics write failed (non-blocking): %s", e)
        return {}

    return record_terminate_metrics_node


def build_graph(plugin: ResearchPlugin, config: dict):
    """
    Compile a LangGraph graph wired with the given plugin's node implementations.

    Args:
        plugin:  An instantiated ResearchPlugin.
        config:  Dict with keys:
                   db_url (str)           — psycopg3 connection string
                   review_interval (int)  — PASS loops between human reviews (default: plugin.get_review_interval())

    Returns:
        Compiled LangGraph graph with PostgresSaver checkpointer.
    """
    from .notify import notify_planka_node

    db_url = config.get("db_url") or os.getenv("DATABASE_URL")
    if not db_url:
        raise ValueError("db_url must be provided in config or DATABASE_URL env var.")

    review_interval = config.get("review_interval", plugin.get_review_interval())

    workflow = StateGraph(ResearchState)

    # --- Nodes ---
    workflow.add_node("plan", plugin.plan_node)
    workflow.add_node("implement", plugin.implement_node)
    workflow.add_node("test", plugin.test_node)
    workflow.add_node("analyze", plugin.analyze_node)
    workflow.add_node("revise", plugin.revise_node)
    workflow.add_node("summarize", plugin.summarize_node)
    workflow.add_node("record_metrics", _make_record_metrics_node(db_url))
    workflow.add_node("record_terminate_metrics", _make_record_terminate_metrics_node(db_url))
    workflow.add_node("notify_planka", notify_planka_node)

    # --- Edges ---
    workflow.add_edge(START, "plan")
    workflow.add_edge("plan", "implement")
    workflow.add_edge("implement", "test")
    workflow.add_edge("test", "analyze")

    workflow.add_conditional_edges(
        "analyze",
        _analyze_router,
        {"fail": "revise", "pass": "summarize", "terminate": "record_terminate_metrics"},
    )

    workflow.add_edge("record_terminate_metrics", END)
    workflow.add_edge("revise", "implement")
    workflow.add_edge("summarize", "record_metrics")

    workflow.add_conditional_edges(
        "record_metrics",
        _make_loop_counter_router(review_interval),
        {"checkpoint": "notify_planka", "continue": "plan"},
    )

    workflow.add_conditional_edges(
        "notify_planka",
        _after_review_router,
        {"terminate": END, "replan": "plan", "continue": "implement"},
    )

    # --- Checkpointer ---
    conn = psycopg.connect(db_url, autocommit=True)
    checkpointer = PostgresSaver(conn)
    checkpointer.setup()  # creates LangGraph's internal checkpoint tables if needed

    compiled = workflow.compile(checkpointer=checkpointer)
    logger.info(
        "Graph compiled for plugin '%s' (review_interval=%d).",
        plugin.name, review_interval,
    )
    return compiled


# ---------------------------------------------------------------------------
# Graph cache (one graph instance per plugin name, shared across requests)
# ---------------------------------------------------------------------------

_graph_cache: dict[str, object] = {}


def get_or_build_graph(plugin: ResearchPlugin, config: dict):
    """Return a cached compiled graph for the given plugin name."""
    key = plugin.name
    if key not in _graph_cache:
        _graph_cache[key] = build_graph(plugin, config)
    return _graph_cache[key]
