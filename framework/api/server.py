"""
framework/api/server.py

FastAPI server for the agentic research framework.

Planka column state machine:
  Planning → Spec Pending Review → Verify → Review → Done / Failed
                ↑ (issues found)    ↑ (loop review continue)
                └── (replan)────────┘

Endpoints:
  POST /planka-webhook  — Planka card-move events
  GET  /health

Webhook routing:
  Spec Pending Review → _run_spec_review_bg  (dual-LLM spec agent)
  Verify / Done        → resume: action=continue
  Failed               → resume: action=terminate

Scheduler:
  Every 60 s: clear review_in_progress flags older than 5 minutes (crash recovery).
"""

import asyncio
import json
import logging
import os
import re
import time

import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from langgraph.types import Command

from framework.db.queries import create_project, get_project, record_checkpoint_decision
from framework.db.connection import run_migration
from framework.plugin_registry import resolve as resolve_plugin
from framework.graph import get_or_build_graph
from framework.spec_clarifier import run_spec_agent, parse_spec_md

logger = logging.getLogger(__name__)

app = FastAPI(title="Agentic Research API", version="0.4.0")

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
        raise ValueError(f"Project '{project_id}' not found.")
    plugin = resolve_plugin(project["plugin_name"])
    config = {"db_url": DATABASE_URL, **(project.get("config") or {}), "planka_sink": _planka_sink}
    return get_or_build_graph(plugin, config)


def _thread_config(project_id: str) -> dict:
    return {"configurable": {"thread_id": project_id}}


def _has_checkpoint(graph, project_id: str) -> bool:
    """Return True only if the graph has an ACTIVE (interrupted) checkpoint."""
    try:
        state = graph.get_state(config=_thread_config(project_id))
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


def _build_initial_state(project: dict) -> dict:
    spec = (project.get("config") or {}).get("spec") or {}
    goal = spec.get("hypothesis") or project.get("goal") or "research"
    return {
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


def _slugify(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


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
        config = {
            "db_url": DATABASE_URL,
            **(project.get("config") or {}),
            "planka_sink": _planka_sink,
        }
        graph = get_or_build_graph(plugin, config)
        _move_planka_card(project_id, _COL_VERIFY)

        graph.invoke(initial_state, config=_thread_config(project_id))

        if _graph_terminated(graph, project_id):
            _move_planka_card(project_id, _COL_PLANNING)
        else:
            logger.info("Graph completed for project '%s'.", project_id)
    except Exception as e:
        logger.exception("Background start failed for project '%s': %s", project_id, e)
        _move_planka_card(project_id, _COL_FAILED)


def _run_spec_review_bg(
    project_id: str,
    card_id: str,
    card_name: str,
    description: str,
) -> None:
    """
    Dual-LLM spec-writing agent gate.
    Called when a card is moved to Spec Pending Review.

    Flow:
      1. Idempotency check (review_in_progress flag).
      2. Ensure thread_id in card description.
      3. Check for .md attachment — reject without it.
      4. Primary LLM: rewrite spec.md, upload new version.
      5. Secondary LLM: consistency review, upload final version.
      6. Both pass → read custom fields, parse spec, upsert project, move to Verify, start graph.
      7. Any issue → comment + move to Planning.
    """
    # --- 1. Idempotency ---
    existing = get_project(project_id, DATABASE_URL)
    if existing and (existing.get("config") or {}).get("review_in_progress"):
        logger.info(
            "Spec review already in progress for '%s', skipping duplicate trigger.", project_id
        )
        return

    try:
        # --- 2. Ensure thread_id in card description ---
        if not _extract_thread_id(description):
            new_desc = f"thread_id: {project_id}\n\n{description}"
            if _planka_sink:
                _planka_sink.update_card_description(project_id, new_desc)

        # --- 3. Upsert project stub + set review_in_progress ---
        create_project(
            project_id=project_id,
            name=card_name,
            plugin_name="unknown",
            goal="",
            config={
                "review_in_progress": True,
                "review_started_at": time.time(),
            },
            db_url=DATABASE_URL,
        )
        if _planka_sink:
            _planka_sink.cache_card_id(project_id, card_id)

        # --- 4. Check for .md attachment — required ---
        spec_md = _planka_sink.download_latest_spec_attachment(card_id) if _planka_sink else None
        if not spec_md:
            if _planka_sink:
                _planka_sink.post_comment(
                    project_id,
                    "**Missing spec.md**\n\n"
                    "Please upload a `spec.md` file as a card attachment before moving to "
                    "Spec Pending Review.\n\n"
                    "Minimum content needed:\n"
                    "- What you want to research\n"
                    "- Your core hypothesis or idea\n"
                    "- Any specific constraints (asset, timeframe, instrument, etc.)",
                )
            _clear_review_flag(project_id)
            _move_planka_card(project_id, _COL_PLANNING)
            return

        llm_chain = _build_llm_chain()
        primary_fn = llm_chain[0][1] if llm_chain else None
        secondary_fn = llm_chain[1][1] if len(llm_chain) > 1 else primary_fn

        # --- 5. Primary LLM: rewrite spec ---
        result1 = run_spec_agent(spec_md, llm_fn=primary_fn, role="primary")
        if _planka_sink:
            _planka_sink.upload_spec_attachment(card_id, "spec.md", result1.enhanced_spec_md)

        if result1.needs_user_input:
            if _planka_sink:
                _planka_sink.post_comment(
                    project_id,
                    "**Spec Agent — Clarification Needed**\n\n"
                    + "\n".join(f"- {q}" for q in result1.questions)
                    + (f"\n\n_{result1.agent_notes}_" if result1.agent_notes else ""),
                )
            _clear_review_flag(project_id)
            _move_planka_card(project_id, _COL_PLANNING)
            return

        # --- 6. Secondary LLM: consistency review ---
        result2 = run_spec_agent(result1.enhanced_spec_md, llm_fn=secondary_fn, role="secondary")
        if _planka_sink:
            _planka_sink.upload_spec_attachment(card_id, "spec.md", result2.enhanced_spec_md)

        if result2.needs_user_input:
            if _planka_sink:
                _planka_sink.post_comment(
                    project_id,
                    "**Spec Agent (Secondary Review) — Issues Found**\n\n"
                    + "\n".join(f"- {q}" for q in result2.questions)
                    + (f"\n\n_{result2.agent_notes}_" if result2.agent_notes else ""),
                )
            _clear_review_flag(project_id)
            _move_planka_card(project_id, _COL_PLANNING)
            return

        # --- 7. Both passed → read custom fields, parse, start ---
        custom_fields = (
            _planka_sink.read_card_custom_fields(card_id) if _planka_sink else {}
        )
        review_interval = int(custom_fields.get("review_interval") or 5)
        max_loops = int(custom_fields.get("max_loops") or 30)

        parsed = parse_spec_md(result2.enhanced_spec_md)
        create_project(
            project_id=project_id,
            name=card_name,
            plugin_name=parsed.get("plugin", "quant_alpha"),
            goal=parsed.get("hypothesis", ""),
            config={
                "spec": parsed,
                "review_interval": review_interval,
                "max_loops": max_loops,
                "review_in_progress": False,
            },
            db_url=DATABASE_URL,
        )

        if _planka_sink:
            _planka_sink.post_comment(
                project_id,
                f"**Spec approved — research starting**\n"
                f"Domain: {result2.domain} | Plugin: {parsed.get('plugin')} | "
                f"Review every {review_interval} loops | Max {max_loops} loops",
            )

        _move_planka_card(project_id, _COL_VERIFY)
        project = get_project(project_id)
        _run_start_bg(project_id, _build_initial_state(project))

    except Exception as e:
        logger.exception("Spec review failed for '%s': %s", project_id, e)
        _clear_review_flag(project_id)
        if _planka_sink:
            _planka_sink.post_comment(
                project_id,
                f"Spec review error: {e}\nCard moved to Failed.",
            )
        _move_planka_card(project_id, _COL_FAILED)


def _clear_review_flag(project_id: str) -> None:
    """Clear review_in_progress flag. Non-blocking."""
    try:
        from framework.db.connection import get_connection
        with get_connection(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE projects SET config = config || %s::jsonb WHERE id = %s",
                    (json.dumps({"review_in_progress": False}), project_id),
                )
    except Exception as e:
        logger.debug("_clear_review_flag failed for '%s': %s", project_id, e)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/planka-webhook")
async def planka_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    Receive Planka card-move events.

    Routing by destination column:
      Spec Pending Review → _run_spec_review_bg (dual-LLM spec agent)
      Verify / Done       → resume: action=continue
      Failed              → resume: action=terminate
    """
    payload = await request.json()
    logger.info("Planka webhook raw payload: %s", payload)

    list_name = (payload.get("list") or {}).get("name", "")
    card = payload.get("item") or {}
    card_id = card.get("id", "")
    description = card.get("description") or ""
    card_name = card.get("name", "")

    # Spec Pending Review: trigger dual-LLM agent
    if list_name == _COL_SPEC_PENDING:
        project_id = _extract_thread_id(description) or _slugify(card_name)
        background_tasks.add_task(
            _run_spec_review_bg, project_id, card_id, card_name, description
        )
        return {"status": "spec_review_queued", "project_id": project_id}

    # Resume routing
    action_map = {
        _COL_VERIFY: "continue",
        _COL_DONE:   "continue",
        _COL_FAILED:  "terminate",
    }
    if list_name not in action_map:
        return {"status": "ignored", "list": list_name}

    project_id = _extract_thread_id(description)
    if not project_id:
        logger.warning("Could not extract thread_id from card %s.", card_id)
        return {"status": "error", "detail": "thread_id not found in card description"}

    project = get_project(project_id)
    if project is None:
        return {"status": "error", "detail": f"Project '{project_id}' not found."}

    notes = _get_latest_card_comment(card_id) if PLANKA_URL and PLANKA_TOKEN else ""
    decision = {"action": action_map[list_name], "notes": notes}

    background_tasks.add_task(_run_resume_bg, project_id, decision)
    logger.info("Planka webhook: project=%s action=%s", project_id, action_map[list_name])
    return {"status": "ok", "project_id": project_id, "action": action_map[list_name]}


@app.get("/health")
async def health():
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Scheduler — crash recovery for stalled spec reviews
# ---------------------------------------------------------------------------

async def _scheduler_loop() -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await _scan_stalled_reviews()
        except Exception as e:
            logger.warning("Scheduler scan error: %s", e)


async def _scan_stalled_reviews() -> None:
    """
    Clear review_in_progress flags that have been set for more than 5 minutes.
    This handles the case where the server crashed mid-review.
    """
    if not DATABASE_URL:
        return
    try:
        from framework.db.connection import get_connection
        stale_cutoff = time.time() - 300  # 5 minutes
        with get_connection(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, config FROM projects
                    WHERE (config->>'review_in_progress')::boolean = true
                    """
                )
                rows = cur.fetchall()
        for project_id, config in rows:
            started_at = (config or {}).get("review_started_at", 0)
            if started_at < stale_cutoff:
                logger.warning(
                    "Clearing stalled review_in_progress for project '%s'.", project_id
                )
                _clear_review_flag(project_id)
                if _planka_sink:
                    _planka_sink.post_comment(
                        project_id,
                        "Spec review timed out — please move the card back to Planning and try again.",
                    )
                _move_planka_card(project_id, _COL_PLANNING)
    except Exception as e:
        logger.warning("_scan_stalled_reviews error: %s", e)


@app.on_event("startup")
async def startup_event():
    global _planka_sink
    _run_migrations()
    _ensure_planka_columns()
    if PLANKA_URL and PLANKA_TOKEN and PLANKA_BOARD_ID:
        from framework.planka import PlankaSink
        _planka_sink = PlankaSink(PLANKA_URL, PLANKA_TOKEN, PLANKA_BOARD_ID, DATABASE_URL)
        _planka_sink.ensure_custom_fields()
        logger.info("PlankaSink initialized with custom fields.")
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
            if _extract_thread_id(card.get("description") or "") == project_id:
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
    """Idempotently create missing Planka columns on startup."""
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
    except Exception as e:
        logger.warning("Could not ensure Planka columns: %s", e)


def _build_llm_chain() -> list[tuple[str, callable]]:
    """Build ordered list of (provider_name, llm_fn) from LLM_CHAIN env var."""
    chain = []
    for provider in (os.getenv("LLM_CHAIN", "") or "").split(","):
        provider = provider.strip()
        if not provider:
            continue
        fn = _try_provider(provider)
        if fn is not None:
            chain.append((provider, fn))
    return chain


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
                    model="claude-sonnet-4-6",
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}],
                )
                return msg.content[0].text
            return _claude
        elif provider in ("codex", "openai"):
            import openai
            client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
            def _openai(prompt: str) -> str:
                resp = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=4096,
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
                    timeout=120,
                )
                r.raise_for_status()
                return r.json().get("response", "")
            return _local
    except Exception as e:
        logger.debug("Provider '%s' unavailable: %s", provider, e)
    return None
