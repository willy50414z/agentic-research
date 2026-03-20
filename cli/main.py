"""
cli/main.py

CLI entry point for the Agentic Research Workflow (runs inside Docker container).

Commands:
    start   — Phase 1: read /workspace/spec.yaml, register project, generate spec.clarified.yaml
    resume  — Phase 1/2: check pending questions or post answers to framework-api
    run     — (internal) Start graph directly with explicit --project/--plugin/--goal flags
    status  — Show the current state of a running / paused project
    approve — Resume a paused graph with a human decision
    plugins — List all registered plugins

Usage examples (from start.sh / resume.sh):
    python cli/main.py start
    python cli/main.py resume

Internal / debug usage:
    python cli/main.py run --project quant_alpha --plugin dummy --goal "find alpha"
    python cli/main.py status --project quant_alpha
    python cli/main.py approve --project quant_alpha --action continue
"""

import json
import os
import sys
import logging
from pathlib import Path

# Ensure /app (repo root inside container) is on the path regardless of cwd
_app_root = str(Path(__file__).parent.parent)
if _app_root not in sys.path:
    sys.path.insert(0, _app_root)

import httpx
import typer
import yaml
from dotenv import load_dotenv

# Load .env from the app root (works whether running inside or outside Docker)
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

# Auto-discover and register all plugins under projects/*/plugin.py
from framework.plugin_registry import discover_plugins as _discover_plugins
_discover_plugins()

from framework.db.queries import create_project, get_project, get_loop_metrics, record_checkpoint_decision
from framework.graph import get_or_build_graph
from framework.plugin_registry import resolve as resolve_plugin, list_plugins
from framework.spec_clarifier import (
    load_spec,
    validate_spec,
    generate_clarifications,
    write_clarified_spec,
    read_clarified_answers,
    load_clarifications,
    all_answered,
    SpecValidationError,
)
from langgraph.types import Command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("cli")

app = typer.Typer(add_completion=False, help="Agentic Research Workflow CLI")

DATABASE_URL = os.getenv("DATABASE_URL", "")
FRAMEWORK_API_URL = os.getenv("FRAMEWORK_API_URL", "http://framework-api:8000")

# Workspace paths (volume-mounted at /workspace when running in Docker)
_WORKSPACE = Path(os.getenv("WORKSPACE_DIR", "/workspace"))
_SPEC_YAML = _WORKSPACE / "spec.yaml"
_SPEC_CLARIFIED = _WORKSPACE / "spec.clarified.yaml"


# ---------------------------------------------------------------------------
# start — Phase 1: read spec.yaml, register, generate spec.clarified.yaml
# ---------------------------------------------------------------------------

@app.command()
def start():
    """
    Phase 1: read /workspace/spec.yaml, register project in DB, create Planka card,
    validate spec, run LLM clarification, and write spec.clarified.yaml.

    Invoked by ./start.sh (short-lived container, exits after writing clarified spec).
    """
    if not DATABASE_URL:
        typer.echo("[ERROR] DATABASE_URL is not set.", err=True)
        raise typer.Exit(1)

    _maybe_run_migration()

    # 1. Load spec
    typer.echo(f"[START] Reading spec from {_SPEC_YAML}")
    try:
        spec = load_spec(_SPEC_YAML)
    except FileNotFoundError as e:
        typer.echo(f"[ERROR] {e}", err=True)
        raise typer.Exit(1)

    # 2. Rule-based validation
    typer.echo("[START] Validating required fields...")
    try:
        validate_spec(spec)
    except SpecValidationError as e:
        typer.echo(f"[ERROR] Spec validation failed:\n{e}", err=True)
        raise typer.Exit(1)
    typer.echo("        ✓ Required fields OK")

    # 3. Register project in DB
    project_id = spec.get("project", {}).get("label", "unknown")
    project_name = spec.get("project", {}).get("name", project_id)
    plugin_name = spec.get("research", {}).get("plugin", "dummy")
    goal = (spec.get("research", {}).get("hypothesis") or "").strip()
    review_interval = spec.get("research", {}).get("review_interval", 5)
    max_loops = spec.get("research", {}).get("max_loops", 30)

    create_project(
        project_id=project_id,
        name=project_name,
        plugin_name=plugin_name,
        goal=goal,
        config={
            "spec": spec,
            "review_interval": review_interval,
            "max_loops": max_loops,
        },
    )
    typer.echo(f"        ✓ Project '{project_id}' registered in DB")

    # 4. Create Planka card in "Clarifying" column
    _create_planka_card(project_id, project_name)

    # 5. LLM semantic analysis — generate clarification questions
    typer.echo("[START] Running LLM semantic analysis...")
    llm_fn = _get_llm_fn()
    clarifications = generate_clarifications(spec, llm_fn=llm_fn)

    # 6. Write spec.clarified.yaml
    write_clarified_spec(_SPEC_CLARIFIED, spec, clarifications)
    typer.echo(f"        ✓ Written: {_SPEC_CLARIFIED}")

    if not clarifications:
        typer.echo("\n[START] No clarifications needed. Run ./resume.sh to start research.")
    else:
        typer.echo(f"\n[START] {len(clarifications)} question(s) generated.")
        typer.echo("        Edit spec.clarified.yaml to fill in answers, then run ./resume.sh")
        for c in clarifications:
            typer.echo(f"\n  Field   : {c['field']}")
            typer.echo(f"  Question: {c['question']}")


# ---------------------------------------------------------------------------
# resume — check state and advance Phase 1 or Phase 2
# ---------------------------------------------------------------------------

@app.command()
def resume():
    """
    Advance the workflow:
      - If unanswered clarifications exist → print them and exit
      - If all answered → POST answers to framework-api /resume to start Phase 2

    Invoked by ./resume.sh (short-lived container).
    """
    if not _SPEC_CLARIFIED.exists():
        typer.echo("[ERROR] spec.clarified.yaml not found. Run ./start.sh first.", err=True)
        raise typer.Exit(1)

    # Load spec to get project_id
    try:
        spec = load_spec(_SPEC_YAML)
    except FileNotFoundError:
        typer.echo("[ERROR] spec.yaml not found.", err=True)
        raise typer.Exit(1)

    project_id = spec.get("project", {}).get("label", "unknown")

    # Step A: check for pending questions
    clarifications = load_clarifications(_SPEC_CLARIFIED)

    if clarifications and not all_answered(clarifications):
        typer.echo(f"\n[RESUME] Project '{project_id}' has unanswered clarifications:\n")
        for c in clarifications:
            answer = (c.get("answer") or "").strip()
            status = "✓" if answer else "✗"
            typer.echo(f"  [{status}] {c['field']}")
            typer.echo(f"       Q: {c['question']}")
            if answer:
                typer.echo(f"       A: {answer}")
        typer.echo(
            "\nEdit spec.clarified.yaml to fill in the remaining answers, "
            "then run ./resume.sh again."
        )
        raise typer.Exit(0)

    # Step B: all answered (or no questions) → post to framework-api
    answers = read_clarified_answers(_SPEC_CLARIFIED)

    typer.echo(f"\n[RESUME] All clarifications answered. Posting to framework-api...")

    payload = {
        "project_id": project_id,
        "decision": {
            "action": "answers",
            "answers": answers,
            "confirmed": True,
        },
    }

    try:
        resp = httpx.post(
            f"{FRAMEWORK_API_URL}/resume",
            json=payload,
            timeout=30,
        )
        if resp.status_code == 409:
            detail = resp.json()
            typer.echo(f"[RESUME] Server confirmation required: {detail}")
            # Re-post with confirmed=true (already set above; server may need explicit flag)
            raise typer.Exit(0)
        resp.raise_for_status()
        data = resp.json()
        typer.echo(f"[RESUME] {data.get('status', 'ok')} — framework-api accepted request.")
        typer.echo("         Research loop starting in background. Check Planka for status.")
    except httpx.HTTPError as e:
        typer.echo(f"[ERROR] Could not reach framework-api: {e}", err=True)
        typer.echo("        Make sure the infra stack is running (./start.sh starts it).")
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# run — internal: start graph with explicit flags (debug / CI use)
# ---------------------------------------------------------------------------

@app.command()
def run(
    project: str = typer.Option(..., "--project", "-p", help="Project ID."),
    plugin: str = typer.Option("dummy", "--plugin", help="Plugin name."),
    goal: str = typer.Option("default research goal", "--goal", "-g", help="Research goal."),
    review_interval: int = typer.Option(0, "--review-interval", help="Override review interval. 0 = plugin default."),
):
    """Start a research project directly (bypasses spec.yaml flow). For debug/CI use."""
    if not DATABASE_URL:
        typer.echo("[ERROR] DATABASE_URL is not set.", err=True)
        raise typer.Exit(1)

    _maybe_run_migration()
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
        "spec": None,
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

    typer.echo(f"\n[RUN] Project '{project}' | Plugin '{plugin}'")
    typer.echo(f"      Goal: {goal}\n")

    graph.invoke(initial_state, config=_thread_config(project))
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


def _print_graph_state(project_id: str, graph) -> None:
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
    else:
        typer.echo(f"\n[PAUSED] Run `approve` to resume.")


def _maybe_run_migration() -> None:
    migration_path = Path(__file__).parent.parent / "db" / "migrations" / "001_business_schema.sql"
    if not migration_path.exists():
        logger.warning("Migration file not found: %s", migration_path)
        return
    try:
        from framework.db.connection import run_migration
        run_migration(str(migration_path))
    except Exception as e:
        logger.warning("Migration skipped (may already be applied): %s", e)


def _get_llm_fn():
    """Return an LLM callable based on available credentials, or None."""
    llm_chain = os.getenv("LLM_CHAIN", "")
    providers = [p.strip() for p in llm_chain.split(",") if p.strip()] if llm_chain else []

    for provider in providers:
        fn = _try_build_llm_fn(provider)
        if fn is not None:
            logger.info("LLM provider selected: %s", provider)
            return fn

    logger.warning("No LLM provider available; using rule-based clarification fallback.")
    return None


def _try_build_llm_fn(provider: str):
    """Try to build an LLM callable for the given provider name."""
    try:
        if provider == "claude":
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                cred_path = os.getenv("CLAUDE_CREDENTIALS_PATH", "")
                if cred_path and Path(cred_path).exists():
                    # Attempt to read key from credentials file
                    creds = yaml.safe_load(Path(cred_path).read_text())
                    api_key = (creds or {}).get("api_key") or (creds or {}).get("ANTHROPIC_API_KEY")
            if not api_key:
                return None
            client = anthropic.Anthropic(api_key=api_key)
            def _claude(prompt: str) -> str:
                msg = client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=256,
                    messages=[{"role": "user", "content": prompt}],
                )
                return msg.content[0].text
            return _claude

        elif provider in ("codex", "openai"):
            import openai
            client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            def _openai(prompt: str) -> str:
                resp = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=256,
                )
                return resp.choices[0].message.content
            return _openai

        elif provider == "gemini":
            import google.generativeai as genai
            api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
            if not api_key:
                return None
            genai.configure(api_key=api_key)
            model = genai.GenerativeModel("gemini-1.5-flash")
            def _gemini(prompt: str) -> str:
                return model.generate_content(prompt).text
            return _gemini

        elif provider in ("local", "opencode"):
            endpoint = os.getenv("LOCAL_LLM_ENDPOINT", "http://localhost:11434")
            model_name = os.getenv("LOCAL_LLM_MODEL", "llama3.2")
            def _local(prompt: str) -> str:
                resp = httpx.post(
                    f"{endpoint}/api/generate",
                    json={"model": model_name, "prompt": prompt, "stream": False},
                    timeout=60,
                )
                resp.raise_for_status()
                return resp.json().get("response", "")
            return _local

    except Exception as e:
        logger.debug("Provider '%s' unavailable: %s", provider, e)
    return None


def _create_planka_card(project_id: str, project_name: str) -> None:
    """Create a Planka card for this project in the 'Clarifying' column."""
    planka_url = os.getenv("PLANKA_API_URL", "")
    planka_token = os.getenv("PLANKA_TOKEN", "")
    planka_board_id = os.getenv("PLANKA_BOARD_ID", "")

    if not (planka_url and planka_token and planka_board_id):
        logger.debug("Planka not configured; skipping card creation.")
        return

    try:
        # Find "Clarifying" list id
        resp = httpx.get(
            f"{planka_url}/api/boards/{planka_board_id}",
            headers={"Authorization": f"Bearer {planka_token}"},
            timeout=10,
        )
        resp.raise_for_status()
        board = resp.json().get("item", {})
        lists = board.get("lists") or []

        list_id = None
        for lst in lists:
            if lst.get("name") == "Clarifying":
                list_id = lst.get("id")
                break

        if not list_id:
            logger.warning("Planka 'Clarifying' column not found on board %s.", planka_board_id)
            return

        httpx.post(
            f"{planka_url}/api/lists/{list_id}/cards",
            headers={"Authorization": f"Bearer {planka_token}"},
            json={
                "name": project_name,
                "description": f"thread_id: {project_id}",
            },
            timeout=10,
        )
        typer.echo(f"        ✓ Planka card created in 'Clarifying'")
    except Exception as e:
        logger.warning("Could not create Planka card: %s", e)


if __name__ == "__main__":
    app()
