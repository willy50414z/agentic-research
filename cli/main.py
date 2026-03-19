"""
cli/main.py

CLI entry point for the Agentic Research Workflow.

Commands:
    start   — Create a new project and kick off the research loop.
    status  — Show the current state of a running / paused project.
    approve — Resume a paused graph with a human decision.
    plugins — List all registered plugins.

Usage examples:
    python cli/main.py start --project quant_alpha --plugin dummy --goal "find alpha"
    python cli/main.py status --project quant_alpha
    python cli/main.py approve --project quant_alpha --action approve
    python cli/main.py approve --project quant_alpha --action continue
    python cli/main.py approve --project quant_alpha --action replan --notes "use ATR filter"
    python cli/main.py approve --project quant_alpha --action terminate
"""

import os
import sys
import logging
from pathlib import Path

# Ensure /app (repo root inside container) is on the path regardless of cwd
_app_root = str(Path(__file__).parent.parent)
if _app_root not in sys.path:
    sys.path.insert(0, _app_root)

import typer
from dotenv import load_dotenv

# Load .env from the app root (works whether running inside or outside Docker)
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# Register all plugins before resolving
# TODO(phase3): replace with importlib auto-discovery (scan projects/*/plugin.py)
import projects.dummy.plugin       # noqa: F401  — triggers @register
import projects.demo.plugin        # noqa: F401  — triggers @register
import projects.quant_alpha.plugin # noqa: F401  — triggers @register

from framework.db.queries import create_project, get_project, get_loop_metrics, record_checkpoint_decision
from framework.graph import get_or_build_graph
from framework.plugin_registry import resolve as resolve_plugin, list_plugins
from langgraph.types import Command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cli")

app = typer.Typer(add_completion=False, help="Agentic Research Workflow CLI")

DATABASE_URL = os.getenv("DATABASE_URL", "")


def _get_graph(project_id: str):
    project = get_project(project_id)
    if not project:
        typer.echo(f"[ERROR] Project '{project_id}' not found in database.", err=True)
        raise typer.Exit(1)
    plugin = resolve_plugin(project["plugin_name"])
    config = {"db_url": DATABASE_URL, **(project.get("config") or {})}
    return get_or_build_graph(plugin, config)


def _thread_config(project_id: str) -> dict:
    return {"configurable": {"thread_id": project_id}}


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

@app.command()
def start(
    project: str = typer.Option(..., "--project", "-p", help="Project ID (used as thread_id)."),
    plugin: str = typer.Option("dummy", "--plugin", help="Plugin name to use."),
    goal: str = typer.Option("default research goal", "--goal", "-g", help="High-level research goal."),
    review_interval: int = typer.Option(0, "--review-interval", help="Override plugin's review interval. 0 = use plugin default."),
):
    """Start a new research project. Runs until the first interrupt (plan review)."""
    if not DATABASE_URL:
        typer.echo("[ERROR] DATABASE_URL is not set.", err=True)
        raise typer.Exit(1)

    # Run migration if needed
    _maybe_run_migration()

    # Create project record
    create_project(project_id=project, name=project, plugin_name=plugin, goal=goal)

    plugin_instance = resolve_plugin(plugin)
    cfg = {"db_url": DATABASE_URL}
    if review_interval > 0:
        cfg["review_interval"] = review_interval

    graph = get_or_build_graph(plugin_instance, cfg)

    initial_state = {
        "project_id": project,
        "loop_index": 0,
        "loop_goal": goal,
        "implementation_plan": None,
        "last_result": "UNKNOWN",
        "last_reason": "",
        "loop_count_since_review": 0,
        "last_checkpoint_decision": None,
        "needs_human_approval": False,
        "attempt_count": 0,
        "test_metrics": {},
        "artifacts": [],
    }

    typer.echo(f"\n[START] Project '{project}' | Plugin '{plugin}'")
    typer.echo(f"        Goal: {goal}\n")

    result = graph.invoke(initial_state, config=_thread_config(project))
    _print_graph_state(project, graph)


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------

@app.command()
def status(
    project: str = typer.Option(..., "--project", "-p", help="Project ID."),
):
    """Show current state and pending interrupt for a project."""
    graph = _get_graph(project)
    _print_graph_state(project, graph)

    # Show loop metrics from DB
    metrics = get_loop_metrics(project)
    if metrics:
        typer.echo("\n--- Loop History ---")
        for m in metrics:
            typer.echo(
                f"  Loop {m['loop_index']:>2}: {m['result']:<5}  "
                f"win_rate={m['win_rate'] or '-':>6}  "
                f"alpha={m['alpha_ratio'] or '-':>6}  "
                f"reason: {(m['reason'] or '')[:60]}"
            )


# ---------------------------------------------------------------------------
# approve
# ---------------------------------------------------------------------------

@app.command()
def approve(
    project: str = typer.Option(..., "--project", "-p", help="Project ID."),
    action: str = typer.Option(..., "--action", "-a",
        help="Decision: approve | reject | continue | replan | terminate"),
    notes: str = typer.Option("", "--notes", "-n", help="Optional notes (for replan)."),
    reason: str = typer.Option("", "--reason", "-r", help="Rejection reason (for reject)."),
):
    """
    Resume a paused graph with a human decision.

    For plan-review (implement node interrupt):
        --action approve
        --action reject --reason "plan is incomplete"

    For loop-review (notify_planka interrupt):
        --action continue
        --action replan --notes "use ATR filter instead of RSI"
        --action terminate
    """
    graph = _get_graph(project)
    state = graph.get_state(config=_thread_config(project))

    if not state.next:
        typer.echo(f"[INFO] Project '{project}' has no pending interrupt. Nothing to resume.")
        raise typer.Exit(0)

    decision: dict = {"action": action}
    if notes:
        decision["notes"] = notes
    if reason:
        decision["reason"] = reason

    typer.echo(f"\n[APPROVE] Project '{project}' | Action '{action}'")
    if notes:
        typer.echo(f"          Notes: {notes}")

    loop_index = state.values.get("loop_index", 0)
    graph.invoke(Command(resume=decision), config=_thread_config(project))

    record_checkpoint_decision(
        project_id=project,
        loop_index=loop_index,
        action=action,
        notes=notes or None,
        modified_plan={"reason": reason} if reason else None,
    )

    _print_graph_state(project, graph)


# ---------------------------------------------------------------------------
# plugins
# ---------------------------------------------------------------------------

@app.command()
def plugins():
    """List all registered plugins."""
    names = list_plugins()
    if names:
        typer.echo("Registered plugins:")
        for n in names:
            typer.echo(f"  - {n}")
    else:
        typer.echo("No plugins registered.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _print_graph_state(project_id: str, graph) -> None:
    """Pretty-print the current LangGraph state and interrupt info."""
    state = graph.get_state(config={"configurable": {"thread_id": project_id}})
    values = state.values or {}
    next_nodes = state.next or []
    tasks = state.tasks or []

    typer.echo(f"\n--- Project: {project_id} ---")
    typer.echo(f"  loop_index          : {values.get('loop_index', 0)}")
    typer.echo(f"  loop_goal           : {values.get('loop_goal', '')[:80]}")
    typer.echo(f"  last_result         : {values.get('last_result', '')}")
    typer.echo(f"  loop_count_since_review: {values.get('loop_count_since_review', 0)}")
    typer.echo(f"  last_checkpoint_decision: {values.get('last_checkpoint_decision')}")
    typer.echo(f"  next_nodes          : {list(next_nodes)}")

    # Show interrupt payload if present
    for task in tasks:
        if hasattr(task, "interrupts") and task.interrupts:
            for interrupt_obj in task.interrupts:
                typer.echo(f"\n[INTERRUPT] Waiting for human input:")
                payload = interrupt_obj.value if hasattr(interrupt_obj, "value") else interrupt_obj
                for k, v in (payload.items() if isinstance(payload, dict) else {}.items()):
                    if k != "instruction":
                        typer.echo(f"  {k}: {str(v)[:120]}")
                if isinstance(payload, dict) and "instruction" in payload:
                    typer.echo(f"\n  {payload['instruction']}")

    if not next_nodes:
        typer.echo("\n[DONE] Graph has completed (reached END).")
    elif next_nodes:
        typer.echo(f"\n[PAUSED] Run `approve` to resume.")


def _maybe_run_migration() -> None:
    """Apply business schema migration if tables don't exist yet."""
    migration_path = Path(__file__).parent.parent / "db" / "migrations" / "001_business_schema.sql"
    if not migration_path.exists():
        logger.warning("Migration file not found: %s", migration_path)
        return
    try:
        from framework.db.connection import run_migration
        run_migration(str(migration_path))
    except Exception as e:
        logger.warning("Migration skipped (may already be applied): %s", e)


if __name__ == "__main__":
    app()
