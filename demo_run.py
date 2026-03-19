"""
demo_run.py — Self-driving end-to-end demo of the Agentic Research Workflow.

Runs entirely in-process (no HTTP calls). Drives the DemoPlugin through:

  Loop 0:  plan → [PLAN REVIEW] → approve → implement → test (FAIL)
                → revise → implement → test (PASS) → summarize → record_metrics

  Loop 1:  plan → [PLAN REVIEW] → approve → implement → test (FAIL)
                → revise → implement → test (PASS) → summarize → record_metrics
                → [LOOP REVIEW reached (interval=2)] → [LOOP REVIEW] → continue

  Loop 2:  plan → [PLAN REVIEW] → approve → implement → test (FAIL)
                → revise → implement → test (PASS) → summarize → record_metrics
                → [LOOP REVIEW reached (interval=2)] → [LOOP REVIEW] → terminate → END

Usage (inside the container):
    python demo_run.py
"""

import logging
import os
import sys
import time
from pathlib import Path

# ── path setup ────────────────────────────────────────────────────────────────
_ROOT = str(Path(__file__).parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(_ROOT) / ".env")

# ── logging: clean, coloured output ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(name)-30s  %(message)s",
    datefmt="%H:%M:%S",
)
# Suppress noisy third-party loggers
for noisy in ("httpx", "httpcore", "psycopg", "psycopg.pool",
              "langgraph", "uvicorn", "asyncio"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

logger = logging.getLogger("demo_run")

# ── imports (trigger plugin registration) ────────────────────────────────────
import projects.demo.plugin   # noqa: F401
import projects.dummy.plugin  # noqa: F401

from framework.db.queries import create_project, get_loop_metrics, record_checkpoint_decision
from framework.graph import get_or_build_graph
from framework.plugin_registry import resolve as resolve_plugin
from langgraph.types import Command

# ── config ────────────────────────────────────────────────────────────────────
PROJECT_ID   = "demo_alpha_001"
PLUGIN_NAME  = "demo"
GOAL         = "find alpha in momentum strategies using synthetic OHLCV data"
DATABASE_URL = os.getenv("DATABASE_URL", "")

_BANNER = "═" * 70


def banner(title: str) -> None:
    logger.info("")
    logger.info(_BANNER)
    logger.info("  %s", title)
    logger.info(_BANNER)


def show_loop_history() -> None:
    metrics = get_loop_metrics(PROJECT_ID)
    if not metrics:
        return
    logger.info("")
    logger.info("  ┌─ Loop History (from loop_metrics table) ─────────────────")
    for m in metrics:
        logger.info(
            "  │  Loop %d  %-5s  win_rate=%-6s  alpha=%-6s  drawdown=%-6s",
            m["loop_index"],
            m["result"],
            f"{float(m['win_rate']):.4f}"  if m["win_rate"]    else "—",
            f"{float(m['alpha_ratio']):.4f}" if m["alpha_ratio"]  else "—",
            f"{float(m['max_drawdown']):.4f}" if m["max_drawdown"] else "—",
        )
    logger.info("  └─────────────────────────────────────────────────────────")


def run_demo() -> None:
    if not DATABASE_URL:
        logger.error("DATABASE_URL is not set. Run inside the container or set the env var.")
        sys.exit(1)

    banner("AGENTIC RESEARCH WORKFLOW — LIVE DEMO")
    logger.info("  Project   : %s", PROJECT_ID)
    logger.info("  Plugin    : %s", PLUGIN_NAME)
    logger.info("  Goal      : %s", GOAL)

    # ── setup ─────────────────────────────────────────────────────────────────
    logger.info("")
    logger.info("[ SETUP ]  Creating project record and building graph …")

    # Run migration
    from framework.db.connection import run_migration
    migration_path = Path(_ROOT) / "db" / "migrations" / "001_business_schema.sql"
    try:
        run_migration(str(migration_path))
        logger.info("[ SETUP ]  Migration applied (or already exists)")
    except Exception as e:
        logger.warning("[ SETUP ]  Migration skipped: %s", e)

    create_project(
        project_id=PROJECT_ID,
        name="Demo Alpha Research",
        plugin_name=PLUGIN_NAME,
        goal=GOAL,
    )
    logger.info("[ SETUP ]  Project '%s' registered in DB", PROJECT_ID)

    plugin = resolve_plugin(PLUGIN_NAME)
    cfg    = {"db_url": DATABASE_URL}
    graph  = get_or_build_graph(plugin, cfg)
    thread = {"configurable": {"thread_id": PROJECT_ID}}

    initial_state = {
        "project_id":               PROJECT_ID,
        "loop_index":               0,
        "loop_goal":                GOAL,
        "implementation_plan":      None,
        "last_result":              "UNKNOWN",
        "last_reason":              "",
        "loop_count_since_review":  0,
        "last_checkpoint_decision": None,
        "needs_human_approval":     False,
        "attempt_count":            0,
        "test_metrics":             {},
        "artifacts":                [],
    }

    # ── Phase 1: start until first interrupt ──────────────────────────────────
    banner("PHASE 1 — START (runs until Plan Review interrupt)")
    graph.invoke(initial_state, config=thread)
    _show_state(graph, thread)

    # ── HITL loop ─────────────────────────────────────────────────────────────
    review_count = 0

    while True:
        state = graph.get_state(config=thread)

        # Nothing pending → graph finished
        if not state.next:
            banner("GRAPH COMPLETED — reached END")
            show_loop_history()
            break

        # Identify interrupt type from task payloads
        interrupt_type = _get_interrupt_type(state)

        if interrupt_type == "plan_review":
            review_count += 1
            banner(f"⏸  PLAN REVIEW (loop {_current_loop(state)})  — AUTO-APPROVING")
            logger.info("  [HUMAN]  action = approve")
            _pause(0.5)
            decision = {"action": "approve"}

        elif interrupt_type == "loop_review":
            loop_num = _current_loop(state)
            if loop_num >= 2:
                banner(f"⏸  LOOP REVIEW  (after loop {loop_num - 1})  — AUTO-TERMINATE")
                logger.info("  [HUMAN]  action = terminate  (demo complete)")
                _pause(0.8)
                decision = {"action": "terminate"}
            else:
                banner(f"⏸  LOOP REVIEW  (after loop {loop_num - 1})  — AUTO-CONTINUE")
                logger.info("  [HUMAN]  action = continue")
                _pause(0.5)
                decision = {"action": "continue"}

        else:
            logger.warning("Unknown interrupt type — defaulting to continue")
            decision = {"action": "continue"}

        # Record human decision
        loop_index = state.values.get("loop_index", 0)
        record_checkpoint_decision(
            project_id=PROJECT_ID,
            loop_index=loop_index,
            action=decision["action"],
            notes=decision.get("notes"),
        )

        # Resume graph
        logger.info("")
        logger.info("[ RESUME ] invoking graph with decision=%s …", decision)
        graph.invoke(Command(resume=decision), config=thread)
        _show_state(graph, thread)


# ── helpers ───────────────────────────────────────────────────────────────────

def _get_interrupt_type(state) -> str:
    for task in (state.tasks or []):
        if hasattr(task, "interrupts") and task.interrupts:
            payload = task.interrupts[0]
            val = payload.value if hasattr(payload, "value") else payload
            if isinstance(val, dict):
                return val.get("checkpoint", "unknown")
    return "unknown"


def _current_loop(state) -> int:
    return (state.values or {}).get("loop_index", 0)


def _show_state(graph, thread: dict) -> None:
    state = graph.get_state(config=thread)
    vals  = state.values or {}
    logger.info("")
    logger.info("  ┌─ Graph State Snapshot ───────────────────────────────────")
    logger.info("  │  loop_index          = %d", vals.get("loop_index", 0))
    logger.info("  │  last_result         = %s", vals.get("last_result", ""))
    logger.info("  │  loop_count_since_review = %d", vals.get("loop_count_since_review", 0))
    logger.info("  │  attempt_count       = %d", vals.get("attempt_count", 0))
    logger.info("  │  artifacts           = %d items", len(vals.get("artifacts") or []))
    logger.info("  │  next_nodes          = %s", list(state.next or []))

    for task in (state.tasks or []):
        if hasattr(task, "interrupts") and task.interrupts:
            p = task.interrupts[0]
            val = p.value if hasattr(p, "value") else p
            if isinstance(val, dict):
                logger.info("  │  ⏸  INTERRUPT  checkpoint=%s", val.get("checkpoint", "?"))
    logger.info("  └─────────────────────────────────────────────────────────")


def _pause(seconds: float) -> None:
    time.sleep(seconds)


if __name__ == "__main__":
    run_demo()