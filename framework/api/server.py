"""
framework/api/server.py

FastAPI server for the agentic research framework.

Planka column state machine:
  Planning → Spec Pending Review → Verify → Review → Done / Failed
                ↑ (issues found)    ↑ (loop review continue)
                └── (replan)────────┘

Endpoints:
  POST /project/init    — create project stub + Planka card in Planning
  POST /start           — parse spec, run LLM review, move to Verify or back to Planning
  POST /resume          — resume paused graph (loop review decisions)
  POST /planka-webhook  — Planka card-move events → resume actions
  GET  /health

Scheduler:
  Every 60 s: scan "Spec Pending Review" column for cards with no checkpoint
  (recovery in case /start background task never fired).
"""

import asyncio
import logging
import os
import re

import httpx
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from pydantic import BaseModel
from langgraph.types import Command

from framework.db.queries import create_project, get_project, record_checkpoint_decision
from framework.db.connection import run_migration
from framework.plugin_registry import resolve as resolve_plugin
from framework.graph import get_or_build_graph
from framework.spec_clarifier import (
    validate_spec,
    generate_clarifications,
    load_spec_md,
    SpecValidationError,
)

logger = logging.getLogger(__name__)

app = FastAPI(title="Agentic Research API", version="0.3.0")

DATABASE_URL = os.getenv("DATABASE_URL", "")
PLANKA_URL = os.getenv("PLANKA_API_URL", "")
PLANKA_TOKEN = os.getenv("PLANKA_TOKEN", "")
PLANKA_BOARD_ID = os.getenv("PLANKA_BOARD_ID", "")

# Planka column names
_COL_PLANNING      = "Planning"
_COL_SPEC_PENDING  = "Spec Pending Review"
_COL_VERIFY        = "Verify"
_COL_REVIEW        = "Review"
_COL_DONE          = "Done"
_COL_FAILED        = "Failed"

# Shared PlankaSink singleton — initialized in startup_event()
_planka_sink = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_graph(project_id: str):
    project = get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")
    plugin = resolve_plugin(project["plugin_name"])
    config = {"db_url": DATABASE_URL, **project.get("config", {}), "planka_sink": _planka_sink}
    return get_or_build_graph(plugin, config)


def _thread_config(project_id: str) -> dict:
    return {"configurable": {"thread_id": project_id}}


def _has_checkpoint(graph, project_id: str) -> bool:
    """Return True only if the graph has an ACTIVE (interrupted) checkpoint."""
    try:
        state = graph.get_state(config=_thread_config(project_id))
        # state.next is non-empty only when the graph is paused at an interrupt
        return bool(state and state.values and state.next)
    except Exception:
        return False


def _graph_terminated(graph, project_id: str) -> bool:
    """Return True if the graph completed with last_result == TERMINATE."""
    try:
        state = graph.get_state(config=_thread_config(project_id))
        if state and state.values:
            return state.values.get("last_result") == "TERMINATE"
    except Exception:
        pass
    return False


def _build_initial_state(project: dict, answers: dict | None = None) -> dict:
    spec = project.get("config", {}).get("spec") or {}
    research = spec.get("research", {})
    goal = research.get("hypothesis") or project.get("goal") or "research"

    state = {
        "project_id": project["id"],
        "loop_index": 0,
        "loop_goal": goal,
        "spec": spec,
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

    if answers:
        state["last_checkpoint_decision"] = {"action": "answers", "answers": answers}

    return state


# ---------------------------------------------------------------------------
# Background task runners
# ---------------------------------------------------------------------------

def _run_resume_bg(project_id: str, decision: dict) -> None:
    """Background: resume a paused graph thread and update Planka card position."""
    try:
        graph = _get_graph(project_id)
        pre_state = graph.get_state(config=_thread_config(project_id))
        loop_index = (pre_state.values or {}).get("loop_index", 0)

        graph.invoke(Command(resume=decision), config=_thread_config(project_id))

        record_checkpoint_decision(
            project_id=project_id,
            loop_index=loop_index,
            action=decision.get("action", ""),
            notes=decision.get("notes") or None,
            modified_plan={k: v for k, v in decision.items()
                           if k not in ("action", "notes")} or None,
            db_url=DATABASE_URL,
        )

        action = decision.get("action", "")
        if action == "terminate":
            _move_planka_card(project_id, _COL_DONE)
        elif _graph_terminated(graph, project_id):
            # Graph ended with TERMINATE (max attempts/loops) → back to Planning
            _move_planka_card(project_id, _COL_PLANNING)
        else:
            _move_planka_card(project_id, _COL_VERIFY)

        logger.info("Resumed project '%s' with action: %s", project_id, action)
    except Exception as e:
        logger.exception("Background resume failed for project '%s': %s", project_id, e)
        _move_planka_card(project_id, _COL_FAILED)


def _run_start_bg(project_id: str, initial_state: dict) -> None:
    """Background: start a new graph thread. Card should already be in Verify."""
    try:
        project = get_project(project_id)
        if not project:
            logger.error("Cannot start: project '%s' not found.", project_id)
            return
        plugin = resolve_plugin(project["plugin_name"])
        config = {"db_url": DATABASE_URL, **(project.get("config") or {}), "planka_sink": _planka_sink}
        graph = get_or_build_graph(plugin, config)

        # Ensure card is in Verify (idempotent move)
        _move_planka_card(project_id, _COL_VERIFY)

        graph.invoke(initial_state, config=_thread_config(project_id))

        if _graph_terminated(graph, project_id):
            _move_planka_card(project_id, _COL_PLANNING)
            logger.info("Graph terminated for project '%s' — card → Planning.", project_id)
        else:
            logger.info("Graph completed for project '%s'.", project_id)
    except Exception as e:
        logger.exception("Background start failed for project '%s': %s", project_id, e)
        _move_planka_card(project_id, _COL_FAILED)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class ProjectInitRequest(BaseModel):
    project_id: str
    project_name: str | None = None


class ResumeRequest(BaseModel):
    project_id: str
    decision: dict


class StartRequest(BaseModel):
    spec_md: str | None = None
    spec: dict | None = None


@app.post("/project/init")
async def project_init(body: ProjectInitRequest):
    """
    Initialize a project stub and create a Planka card in Planning.

    Called by `agentic-research init` after creating the local directory.
    No spec is required yet — user will write spec.md and then run ./start.sh.
    """
    project_id = body.project_id
    project_name = body.project_name or body.project_id.replace("-", " ").title()

    create_project(
        project_id=project_id,
        name=project_name,
        plugin_name="dummy",
        goal="",
        config={},
        db_url=DATABASE_URL,
    )

    _move_planka_card_to_column(project_id, project_name, _COL_PLANNING)

    logger.info("Project '%s' initialised (Planning).", project_id)
    return {"project_id": project_id, "status": "initialized"}


@app.post("/start")
async def start_project(body: StartRequest, background_tasks: BackgroundTasks):
    """
    Parse and validate spec, run LLM clarification review.

    State transitions:
      card → Spec Pending Review (immediately)
      → Planning          (if clarifications needed — user must re-run after editing)
      → Verify + graph    (if no clarifications — research loop starts)

    Accepts spec_md (markdown string) or spec (pre-parsed dict).
    """
    llm_fn = _build_llm_fn()

    if body.spec_md:
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
            f.write(body.spec_md)
            tmp = f.name
        try:
            spec = load_spec_md(tmp, llm_fn=llm_fn)
        finally:
            os.unlink(tmp)
    elif body.spec:
        spec = body.spec
    else:
        raise HTTPException(status_code=422, detail="Either spec_md or spec must be provided.")

    try:
        validate_spec(spec)
    except SpecValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    project_section = spec.get("project", {})
    research_section = spec.get("research", {})

    project_id = project_section.get("label", "unknown")
    project_name = project_section.get("name", project_id)
    plugin_name = research_section.get("plugin", "dummy")
    goal = (research_section.get("hypothesis") or "").strip()
    review_interval = research_section.get("review_interval", 5)
    max_loops = research_section.get("max_loops", 30)

    # Upsert project (updates if already created by /project/init)
    create_project(
        project_id=project_id,
        name=project_name,
        plugin_name=plugin_name,
        goal=goal,
        config={"spec": spec, "review_interval": review_interval, "max_loops": max_loops},
        db_url=DATABASE_URL,
    )

    # Move card → Spec Pending Review
    _move_planka_card_to_column(project_id, project_name, _COL_SPEC_PENDING)

    # Upload spec content to card description
    _upload_spec_to_planka(project_id, body.spec_md or "")

    # LLM spec review
    clarifications = generate_clarifications(spec, llm_fn=llm_fn)

    if clarifications:
        # Issues found: move card back to Planning for user to address
        _move_planka_card(project_id, _COL_PLANNING)
        logger.info(
            "Project '%s': %d clarification(s) needed, card back to Planning.",
            project_id, len(clarifications),
        )
        return {"project_id": project_id, "spec": spec, "clarifications": clarifications}

    # No issues: start research loop
    _move_planka_card(project_id, _COL_VERIFY)
    project = get_project(project_id)
    initial_state = _build_initial_state(project)
    background_tasks.add_task(_run_start_bg, project_id, initial_state)
    logger.info("Project '%s': spec clean, graph starting in Verify.", project_id)
    return {
        "project_id": project_id,
        "spec": spec,
        "clarifications": [],
        "status": "started",
    }


@app.post("/resume")
async def resume(body: ResumeRequest, background_tasks: BackgroundTasks):
    """
    Resume a paused LangGraph thread or start with confirmed clarification answers.

    Routing:
      Has checkpoint → resume in background (loop review decisions)
      No checkpoint + confirmed=true → start graph in background (after user answered clarifications via ./resume.sh)
      No checkpoint + not confirmed → 409 confirmation_required
    """
    project_id = body.project_id
    decision = body.decision

    project = get_project(project_id)
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project '{project_id}' not found.")

    plugin = resolve_plugin(project["plugin_name"])
    config = {"db_url": DATABASE_URL, **(project.get("config") or {}), "planka_sink": _planka_sink}
    graph = get_or_build_graph(plugin, config)

    if _has_checkpoint(graph, project_id):
        background_tasks.add_task(_run_resume_bg, project_id, decision)
        logger.info("Queued resume for project '%s'.", project_id)
        return {"status": "resuming", "project_id": project_id}

    # No checkpoint — must confirm before starting
    if not decision.get("confirmed"):
        raise HTTPException(
            status_code=409,
            detail={"status": "confirmation_required", "project_id": project_id},
        )

    answers = decision.get("answers") or {}
    initial_state = _build_initial_state(project, answers)
    background_tasks.add_task(_run_start_bg, project_id, initial_state)
    logger.info("Queued start for project '%s' (answers confirmed).", project_id)
    return {"status": "starting", "project_id": project_id}


@app.post("/planka-webhook")
async def planka_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive Planka card-move events and translate to resume actions.

    Recognized destination columns:
      Review → (no-op, just tracking)
      Done / Verify  → action: continue
      Failed         → action: terminate

    The card description must contain:  thread_id: <project_id>
    """
    payload = await request.json()

    list_name = (payload.get("list") or {}).get("name", "")
    action_map = {
        _COL_VERIFY: "continue",
        _COL_DONE:   "continue",
        _COL_FAILED:  "terminate",
    }

    if list_name not in action_map:
        return {"status": "ignored", "list": list_name}

    card = payload.get("item") or {}
    card_id = card.get("id")
    description = card.get("description", "")

    project_id = _extract_thread_id(description)
    if not project_id:
        logger.warning("Could not extract thread_id from card %s.", card_id)
        return {"status": "error", "detail": "thread_id not found in card description"}

    action = action_map[list_name]
    notes = _get_latest_card_comment(card_id) if PLANKA_URL and PLANKA_TOKEN else ""

    decision = {"action": action, "notes": notes}

    project = get_project(project_id)
    if project is None:
        return {"status": "error", "detail": f"Project '{project_id}' not found."}

    background_tasks.add_task(_run_resume_bg, project_id, decision)
    logger.info("Planka webhook: project=%s action=%s", project_id, action)
    return {"status": "ok", "project_id": project_id, "action": action}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Scheduler — recovery scan for Spec Pending Review cards
# ---------------------------------------------------------------------------

async def _scheduler_loop() -> None:
    """
    Every 60 s: scan "Spec Pending Review" for cards with no LangGraph checkpoint.
    This is a recovery mechanism in case the server restarted mid-flow.
    """
    while True:
        await asyncio.sleep(60)
        try:
            await _scan_spec_pending_projects()
        except Exception as e:
            logger.warning("Scheduler scan error: %s", e)


async def _scan_spec_pending_projects() -> None:
    if not (PLANKA_URL and PLANKA_TOKEN and PLANKA_BOARD_ID):
        return

    cards = _get_planka_column_cards(_COL_SPEC_PENDING)
    for card in cards:
        description = card.get("description", "")
        project_id = _extract_thread_id(description)
        if not project_id:
            continue

        project = get_project(project_id)
        if not project:
            continue

        try:
            plugin = resolve_plugin(project["plugin_name"])
            config = {"db_url": DATABASE_URL, **(project.get("config") or {})}
            graph = get_or_build_graph(plugin, config)

            if not _has_checkpoint(graph, project_id):
                spec = (project.get("config") or {}).get("spec")
                if not spec:
                    logger.warning(
                        "Scheduler: project '%s' in Spec Pending Review has no spec in DB — skipping.",
                        project_id,
                    )
                    continue

                clarifications = generate_clarifications(spec, llm_fn=_build_llm_fn())
                if clarifications:
                    _move_planka_card(project_id, _COL_PLANNING)
                    logger.info(
                        "Scheduler: project '%s' has clarifications, moved to Planning.", project_id
                    )
                else:
                    _move_planka_card(project_id, _COL_VERIFY)
                    initial_state = _build_initial_state(project)
                    asyncio.get_event_loop().run_in_executor(
                        None, _run_start_bg, project_id, initial_state
                    )
                    logger.info("Scheduler: auto-started project '%s'.", project_id)
        except Exception as e:
            logger.warning("Scheduler could not process project '%s': %s", project_id, e)


@app.on_event("startup")
async def startup_event():
    global _planka_sink
    _run_migrations()
    _ensure_planka_columns()
    if PLANKA_URL and PLANKA_TOKEN and PLANKA_BOARD_ID:
        from framework.planka import PlankaSink
        _planka_sink = PlankaSink(PLANKA_URL, PLANKA_TOKEN, PLANKA_BOARD_ID, DATABASE_URL)
        logger.info("PlankaSink initialized.")
    asyncio.create_task(_scheduler_loop())
    logger.info("Scheduler started.")


def _run_migrations() -> None:
    import pathlib
    migrations_dir = pathlib.Path(__file__).parent.parent.parent / "db" / "migrations"
    if not migrations_dir.exists():
        logger.warning("Migrations directory not found: %s", migrations_dir)
        return
    for sql_file in sorted(migrations_dir.glob("*.sql")):
        try:
            run_migration(str(sql_file))
            logger.info("Migration applied: %s", sql_file.name)
        except Exception as e:
            logger.error("Migration failed (%s): %s", sql_file.name, e)
            raise


# ---------------------------------------------------------------------------
# Planka helpers
# ---------------------------------------------------------------------------

def _upload_spec_to_planka(project_id: str, spec_md: str) -> None:
    """Update the Planka card description with the full spec content."""
    if not _planka_sink or not spec_md:
        return
    description = f"thread_id: {project_id}\n\n---\n\n{spec_md[:10_000]}"
    _planka_sink.update_card_description(project_id, description)


def _extract_thread_id(description: str) -> str | None:
    match = re.search(r"thread_id:\s*(\S+)", description)
    return match.group(1) if match else None


def _get_latest_card_comment(card_id: str) -> str:
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


def _get_planka_column_cards(column_name: str) -> list[dict]:
    if not (PLANKA_URL and PLANKA_TOKEN and PLANKA_BOARD_ID):
        return []
    try:
        resp = httpx.get(
            f"{PLANKA_URL}/api/boards/{PLANKA_BOARD_ID}",
            headers={"Authorization": f"Bearer {PLANKA_TOKEN}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        lists = data.get("included", {}).get("lists") or []
        cards = data.get("included", {}).get("cards") or []
        for lst in lists:
            if lst.get("name") == column_name:
                return [c for c in cards if c.get("listId") == lst["id"]]
    except Exception as e:
        logger.warning("Could not fetch Planka column '%s': %s", column_name, e)
    return []


def _move_planka_card_to_column(project_id: str, project_name: str, column_name: str) -> None:
    """Create card in column if it doesn't exist; move it there if it does."""
    if not (PLANKA_URL and PLANKA_TOKEN and PLANKA_BOARD_ID):
        return
    try:
        resp = httpx.get(
            f"{PLANKA_URL}/api/boards/{PLANKA_BOARD_ID}",
            headers={"Authorization": f"Bearer {PLANKA_TOKEN}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        lists = data.get("included", {}).get("lists") or []
        cards = data.get("included", {}).get("cards") or []

        target_list_id = None
        existing_card_id = None

        for lst in lists:
            if lst.get("name") == column_name:
                target_list_id = lst.get("id")
        for card in cards:
            if _extract_thread_id(card.get("description", "")) == project_id:
                existing_card_id = card.get("id")

        if not target_list_id:
            logger.warning("Planka column '%s' not found on board %s.", column_name, PLANKA_BOARD_ID)
            return

        if existing_card_id:
            if _planka_sink:
                _planka_sink.cache_card_id(project_id, existing_card_id)
            httpx.patch(
                f"{PLANKA_URL}/api/cards/{existing_card_id}",
                headers={"Authorization": f"Bearer {PLANKA_TOKEN}"},
                json={"listId": target_list_id, "position": 65535},
                timeout=10,
            )
        else:
            r = httpx.post(
                f"{PLANKA_URL}/api/lists/{target_list_id}/cards",
                headers={"Authorization": f"Bearer {PLANKA_TOKEN}"},
                json={
                    "name": project_name,
                    "description": f"thread_id: {project_id}",
                    "type": "project",
                    "position": 65535,
                },
                timeout=10,
            )
            try:
                new_card_id = r.json().get("item", {}).get("id")
                if new_card_id and _planka_sink:
                    _planka_sink.cache_card_id(project_id, new_card_id)
            except Exception:
                pass
        logger.info("Planka card for project '%s' placed in '%s'.", project_id, column_name)
    except Exception as e:
        logger.warning("Planka card operation failed for project '%s': %s", project_id, e)


def _move_planka_card(project_id: str, column_name: str) -> None:
    """Move existing Planka card (identified by thread_id) to a named column."""
    if not (PLANKA_URL and PLANKA_TOKEN and PLANKA_BOARD_ID):
        return
    try:
        resp = httpx.get(
            f"{PLANKA_URL}/api/boards/{PLANKA_BOARD_ID}",
            headers={"Authorization": f"Bearer {PLANKA_TOKEN}"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        lists = data.get("included", {}).get("lists") or []
        cards = data.get("included", {}).get("cards") or []

        target_list_id = None
        card_id = None

        for lst in lists:
            if lst.get("name") == column_name:
                target_list_id = lst.get("id")
        for card in cards:
            if _extract_thread_id(card.get("description", "")) == project_id:
                card_id = card.get("id")

        if card_id and target_list_id:
            if _planka_sink:
                _planka_sink.cache_card_id(project_id, card_id)
            httpx.patch(
                f"{PLANKA_URL}/api/cards/{card_id}",
                headers={"Authorization": f"Bearer {PLANKA_TOKEN}"},
                json={"listId": target_list_id, "position": 65535},
                timeout=10,
            )
            logger.info("Moved Planka card for project '%s' to '%s'.", project_id, column_name)
        elif not card_id:
            logger.warning("No Planka card found for project '%s'.", project_id)
        elif not target_list_id:
            logger.warning("Planka column '%s' not found.", column_name)
    except Exception as e:
        logger.warning("Could not move Planka card for project '%s': %s", project_id, e)


def _ensure_planka_columns() -> None:
    """
    Idempotently create missing Planka columns on startup.

    Required columns (in order):
      Planning | Spec Pending Review | Verify | Review | Done | Failed

    Safe to call repeatedly — only creates columns that are absent.
    """
    if not (PLANKA_URL and PLANKA_TOKEN and PLANKA_BOARD_ID):
        logger.info("Planka not configured — skipping column setup.")
        return

    required = [
        (_COL_PLANNING,     10000),
        (_COL_SPEC_PENDING, 20000),
        (_COL_VERIFY,       25000),
        (_COL_REVIEW,       30000),
        (_COL_DONE,         40000),
        (_COL_FAILED,       50000),
    ]

    try:
        resp = httpx.get(
            f"{PLANKA_URL}/api/boards/{PLANKA_BOARD_ID}",
            headers={"Authorization": f"Bearer {PLANKA_TOKEN}"},
            timeout=10,
        )
        resp.raise_for_status()
        existing_names = {
            lst["name"]
            for lst in (resp.json().get("included", {}).get("lists") or [])
        }

        for name, position in required:
            if name not in existing_names:
                r = httpx.post(
                    f"{PLANKA_URL}/api/boards/{PLANKA_BOARD_ID}/lists",
                    headers={"Authorization": f"Bearer {PLANKA_TOKEN}"},
                    json={"name": name, "position": position, "type": "active"},
                    timeout=10,
                )
                if r.status_code == 200:
                    logger.info("Created Planka column '%s'.", name)
                else:
                    logger.warning("Failed to create Planka column '%s': %s", name, r.text[:100])
            else:
                logger.debug("Planka column '%s' already exists.", name)
    except Exception as e:
        logger.warning("Could not ensure Planka columns: %s", e)


def _build_llm_fn():
    llm_chain = os.getenv("LLM_CHAIN", "")
    providers = [p.strip() for p in llm_chain.split(",") if p.strip()]
    for provider in providers:
        fn = _try_provider(provider)
        if fn is not None:
            logger.info("LLM provider selected: %s", provider)
            return fn
    logger.warning("No LLM provider available; using rule-based clarification fallback.")
    return None


def _try_provider(provider: str):
    try:
        if provider == "claude":
            import anthropic
            api_key = os.getenv("ANTHROPIC_API_KEY")
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
                r = httpx.post(
                    f"{endpoint}/api/generate",
                    json={"model": model_name, "prompt": prompt, "stream": False},
                    timeout=60,
                )
                r.raise_for_status()
                return r.json().get("response", "")
            return _local
    except Exception as e:
        logger.debug("Provider '%s' unavailable: %s", provider, e)
    return None
