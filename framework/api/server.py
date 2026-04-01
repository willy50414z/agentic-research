"""
framework/api/server.py

FastAPI server for the agentic research framework.

Planka column state machine:
  Planning → Spec Pending Review → Verify → Review → Done / Failed

Endpoints:
  POST /planka-webhook  — Planka card-move events
  GET  /health

Webhook routing:
  Spec Pending Review → _run_spec_review_bg  (dual-LLM spec agent)
  Verify              → fresh start (if no active checkpoint) or resume
  Failed              → resume: action=terminate

End conditions after graph completes:
  last_result == "PASS"      → card moves to Done
  last_result == "TERMINATE" → card moves to Review + TERMINATE reason posted as comment

Scheduler:
  Every 60 s: clear review_in_progress flags older than 5 minutes (crash recovery).
"""

import asyncio
import json
import logging
import os
import re
import time
from pathlib import Path

import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks
from langgraph.types import Command

from framework.db.queries import create_project, get_project
from framework.plugin_registry import resolve as resolve_plugin
from framework.graph import get_or_build_graph
from framework.spec_clarifier import run_spec_agent, parse_spec_md, SpecAgentResult
from framework.llm_preflight import preflight_check, get_preflight_results
from framework.spec_review_graph import get_or_build_spec_review_graph, SpecReviewState

logger = logging.getLogger(__name__)

@asynccontextmanager
async def _lifespan(app: FastAPI):
    global _planka_sink
    _ensure_planka_columns()
    if PLANKA_URL and PLANKA_TOKEN and PLANKA_BOARD_ID:
        from framework.planka import PlankaSink
        _planka_sink = PlankaSink(PLANKA_URL, PLANKA_TOKEN, PLANKA_BOARD_ID, DATABASE_URL)
        _planka_sink.ensure_custom_fields()
        logger.info("PlankaSink initialized with custom fields.")

    # --- Preflight: verify all required services before accepting traffic ---
    llm_chain = os.getenv("LLM_CHAIN", "")
    results = preflight_check(
        db_url=DATABASE_URL,
        planka_url=PLANKA_URL,
        planka_token=PLANKA_TOKEN,
        llm_chain_str=llm_chain,
    )
    app.state.preflight = results
    logger.info("Preflight passed: %s", list(results.keys()))

    task = asyncio.create_task(_scheduler_loop())
    logger.info("Scheduler started.")
    yield
    task.cancel()


app = FastAPI(title="Agentic Research API", version="0.4.0", lifespan=_lifespan)

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


def _build_initial_state(project: dict) -> dict:
    cfg = project.get("config") or {}
    spec = cfg.get("spec") or {}
    goal = spec.get("hypothesis") or project.get("goal") or "research"
    return {
        "project_id": project["id"],
        "loop_index": 0,
        "loop_goal": goal,
        "spec": spec,
        "implementation_plan": None,
        "last_result": "UNKNOWN",
        "last_reason": "",
        "max_loops": int(cfg.get("max_loops") or 3),
        "attempt_index": 0,
        "needs_human_approval": False,
        "attempt_count": 0,
        "test_metrics": {},
        "artifacts": [],
    }


def _slugify(name: str) -> str:
    import unicodedata
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


# ---------------------------------------------------------------------------
# Background task runners
# ---------------------------------------------------------------------------

def _run_resume_bg(project_id: str, decision: dict) -> None:
    """Background: resume a paused graph thread (plan-approval interrupt) and update card."""
    try:
        graph = _get_graph(project_id)
        graph.invoke(Command(resume=decision), config=_thread_config(project_id))

        state = graph.get_state(config=_thread_config(project_id))
        vals = (state.values or {}) if state else {}
        last_result = vals.get("last_result", "UNKNOWN")
        last_reason = vals.get("last_reason", "")

        if state and state.next:
            last_result = vals.get("last_result", "UNKNOWN")
            loop_index = vals.get("loop_index", "?")
            logger.info(
                "[resume_bg] project='%s' RE-INTERRUPTED at %s  last_result=%s  loop=%s — moving to Review.",
                project_id, list(state.next), last_result, loop_index,
            )
            if _planka_sink:
                _planka_sink.post_comment(
                    project_id,
                    f"**計畫待審核** — 請確認上方計畫後，將卡片移回 **Verify** 繼續執行，"
                    f"或留在 **Review** 暫停。\n\n等待節點：`{'`, `'.join(state.next)}`",
                )
            _move_planka_card(project_id, _COL_REVIEW)
            return

        _finish_run(project_id, last_result, last_reason)
        logger.info("Resumed project '%s' with action: %s", project_id, decision.get("action"))
    except Exception as e:
        logger.exception("Background resume failed for project '%s': %s", project_id, e)
        _move_planka_card(project_id, _COL_FAILED)


def _finish_run(project_id: str, last_result: str, last_reason: str) -> None:
    """Move card to Done/Review/Planning based on graph's final last_result."""
    if last_result == "PASS":
        _move_planka_card(project_id, _COL_DONE)
    elif last_result == "TERMINATE":
        if _planka_sink:
            _planka_sink.post_comment(
                project_id,
                f"**Research ended — moved to Review**\n\nReason: {last_reason[:500]}",
            )
        _move_planka_card(project_id, _COL_REVIEW)
    else:
        _move_planka_card(project_id, _COL_PLANNING)


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

        graph.invoke(initial_state, config=_thread_config(project_id))

        state = graph.get_state(config=_thread_config(project_id))
        vals = (state.values or {}) if state else {}
        last_result = vals.get("last_result", "UNKNOWN")
        last_reason = vals.get("last_reason", "")

        # If the graph paused at a human-in-the-loop interrupt (state.next is non-empty),
        # move to Review so the user can inspect the plan and move the card back to Verify to approve.
        if state and state.next:
            last_result = vals.get("last_result", "UNKNOWN")
            loop_index = vals.get("loop_index", "?")
            logger.info(
                "[start_bg] project='%s' INTERRUPTED at %s  last_result=%s  loop=%s — moving to Review.",
                project_id, list(state.next), last_result, loop_index,
            )
            if _planka_sink:
                _planka_sink.post_comment(
                    project_id,
                    f"**計畫待審核** — 請確認上方計畫後，將卡片移回 **Verify** 繼續執行，"
                    f"或留在 **Review** 暫停。\n\n等待節點：`{'`, `'.join(state.next)}`",
                )
            _move_planka_card(project_id, _COL_REVIEW)
            return

        _finish_run(project_id, last_result, last_reason)
        logger.info("Graph completed for project '%s' with result: %s", project_id, last_result)
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
    Spec review gate — invokes spec_review_graph (LangGraph).

    Pre-graph setup (idempotency, card_id cache, spec download) is done here.
    All LLM calls, role routing, and Planka finalization happen inside the graph
    with PostgresSaver checkpointing for error resume.

    Flow:
      1. Idempotency check (review_in_progress flag).
      2. Cache card_id, upsert project stub.
      3. Ensure thread_id in card description.
      4. Download spec.md attachment — abort if missing.
      5. Invoke spec_review_graph with initial state.
    """
    logger.info("[spec-review] START  card='%s' project_id='%s'", card_name, project_id)

    # --- 1. Idempotency ---
    existing = get_project(project_id, DATABASE_URL)
    if existing and (existing.get("config") or {}).get("review_in_progress"):
        started_at = (existing.get("config") or {}).get("review_started_at") or 0
        age_seconds = time.time() - started_at
        if age_seconds < 300:
            logger.warning(
                "[spec-review] SKIP  review already in progress for '%s' (started %.0fs ago).",
                project_id, age_seconds,
            )
            _clear_review_flag(project_id)
            _move_planka_card(project_id, _COL_PLANNING)
            return
        else:
            logger.warning(
                "[spec-review] stale review_in_progress for '%s' (%.0fs old), clearing.",
                project_id, age_seconds,
            )
            _clear_review_flag(project_id)

    try:
        # --- 2. Cache card_id + upsert project stub ---
        if _planka_sink:
            _planka_sink.cache_card_id(project_id, card_id)

        create_project(
            project_id=project_id,
            name=card_name,
            plugin_name="unknown",
            goal="",
            config={"review_in_progress": True, "review_started_at": time.time()},
            db_url=DATABASE_URL,
        )

        # --- 3. Ensure thread_id in card description ---
        if not _extract_thread_id(description):
            new_desc = f"thread_id: {project_id}\n\n{description or ''}"
            if _planka_sink:
                _planka_sink.update_card_description(project_id, new_desc)

        # --- 4. Download spec.md attachment ---
        spec_path = _planka_sink.download_latest_spec_attachment(card_id) if _planka_sink else None
        if not spec_path:
            logger.warning("[spec-review] ABORT  no spec.md attachment for card '%s'.", card_name)
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
        logger.info("[spec-review] spec.md saved to %s", spec_path)

        # --- 5. Fetch Planka comment thread for Q&A detection ---
        planka_comments: list = []
        if _planka_sink:
            planka_comments = _planka_sink.get_card_comments(card_id)
            logger.info("[spec-review] fetched %d comment(s) from card '%s'", len(planka_comments), card_id)

        # --- 6. Invoke spec_review_graph ---
        initial_state: SpecReviewState = {
            "project_id": project_id,
            "card_id": card_id,
            "spec_path": spec_path,
            "participants": [],
            "current_round": 0,
            "total_rounds": 0,
            "current_spec_md": "",
            "review_notes": [],
            "status": "in_progress",
            "questions": [],
            "planka_comments": planka_comments,
            "has_pending_qa": False,
        }
        graph_config = {
            "configurable": {
                "thread_id": project_id,
                "db_url": DATABASE_URL,
                "planka_sink": _planka_sink,
                "move_card_fn": _move_planka_card,
            }
        }
        graph = get_or_build_spec_review_graph({"db_url": DATABASE_URL})
        graph.invoke(initial_state, config=graph_config)
        _clear_review_flag(project_id)
        logger.info("[spec-review] DONE  graph completed for project '%s'.", project_id)

        if spec_path and _planka_sink:
            _upload_work_dir_files(card_id, str(Path(spec_path).parent))

    except Exception as e:
        logger.exception("[spec-review] ERROR  card='%s' unhandled exception: %s", card_name, e)
        _clear_review_flag(project_id)
        _post_error_and_move_planning(project_id, "Spec review", e)


_SKIP_UPLOAD = {
    # Flow-control status files — never shown to user
    "status_pass.txt",
    "status_need_update.txt",
    # Original spec — already on the card; re-uploading creates a duplicate
    "spec.md",
    # Rules file copied into work_dir by test scripts
    "spec-review.md",
    # Working copy written during review rounds — intermediate artifact
    "current_spec_for_review.md",
}


def _upload_work_dir_files(card_id: str, work_dir: str) -> None:
    """Upload every file in work_dir (except status control files) to the Planka card, then delete the directory."""
    import shutil
    from pathlib import Path
    work_path = Path(work_dir)
    for fpath in sorted(work_path.iterdir()):
        if not fpath.is_file():
            continue
        if fpath.name in _SKIP_UPLOAD:
            continue
        try:
            content = fpath.read_text(encoding="utf-8")
            _planka_sink.upload_spec_attachment(card_id, fpath.name, content)
            logger.info("Uploaded work_dir file '%s' to card '%s'.", fpath.name, card_id)
        except Exception as e:
            logger.warning("Failed to upload '%s': %s", fpath.name, e)
    try:
        shutil.rmtree(work_path)
        logger.info("Deleted work_dir '%s' after upload.", work_path)
    except Exception as e:
        logger.warning("Failed to delete work_dir '%s': %s", work_path, e)


def _post_error_and_move_planning(project_id: str, stage: str, exc: Exception) -> None:
    """Post an error comment on the card and move it to Planning so the user can act."""
    import traceback
    detail = traceback.format_exc()[-1500:]  # last 1500 chars to stay within comment limits
    if _planka_sink:
        _planka_sink.post_comment(
            project_id,
            f"**{stage} error — moved to Planning**\n\n"
            f"```\n{exc}\n```\n\n"
            f"<details><summary>Traceback</summary>\n\n```\n{detail}\n```\n</details>",
        )
    _move_planka_card(project_id, _COL_PLANNING)


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
    # logger.info("Planka webhook raw payload: %s", payload)

    # Only process cardUpdate events (card moved to a column)
    event = payload.get("event", "")
    if event != "cardUpdate":
        return {"status": "ignored", "event": event}

    data = payload.get("data") or {}
    card = data.get("item") or {}
    card_id = card.get("id", "")
    description = card.get("description") or ""
    card_name = card.get("name", "")

    # Resolve destination list name from included lists
    included = data.get("included") or {}
    lists_included = included.get("lists") or []
    current_list_id = card.get("listId", "")
    list_name = next(
        (lst.get("name", "") for lst in lists_included if lst.get("id") == current_list_id),
        "",
    )

    # Only process if the card actually moved to a different list
    prev_data = payload.get("prevData") or {}
    prev_list_id = (prev_data.get("item") or {}).get("listId", "")
    if not list_name or current_list_id == prev_list_id:
        return {"status": "ignored", "reason": "not a list change"}

    logger.info("Card '%s' moved to list '%s'", card_name, list_name)

    # Spec Pending Review: trigger dual-LLM agent
    if list_name == _COL_SPEC_PENDING:
        project_id = _extract_thread_id(description) or _slugify(card_name) or card_id
        background_tasks.add_task(
            _run_spec_review_bg, project_id, card_id, card_name, description
        )
        return {"status": "spec_review_queued", "project_id": project_id}

    # Only handle Verify and Failed columns
    if list_name not in (_COL_VERIFY, _COL_FAILED):
        return {"status": "ignored", "list": list_name}

    project_id = _extract_thread_id(description)
    if not project_id:
        logger.warning("Could not extract thread_id from card %s.", card_id)
        return {"status": "error", "detail": "thread_id not found in card description"}

    project = get_project(project_id)
    if project is None:
        return {"status": "error", "detail": f"Project '{project_id}' not found."}

    if list_name == _COL_VERIFY:
        try:
            graph = _get_graph(project_id)
            state = graph.get_state(config=_thread_config(project_id))
            has_checkpoint = bool(state and state.values and state.next)
        except Exception as e:
            logger.warning("[verify] failed to read graph state for '%s': %s", project_id, e)
            has_checkpoint = False
            state = None

        if has_checkpoint:
            next_nodes = list(state.next) if state else []
            last_result = (state.values or {}).get("last_result", "UNKNOWN") if state else "UNKNOWN"
            loop_index = (state.values or {}).get("loop_index", "?") if state else "?"
            logger.info(
                "[verify] project='%s' RESUME checkpoint  next=%s  last_result=%s  loop=%s",
                project_id, next_nodes, last_result, loop_index,
            )
            notes = _get_latest_card_comment(card_id) if PLANKA_URL and PLANKA_TOKEN else ""
            background_tasks.add_task(_run_resume_bg, project_id, {"action": "continue", "notes": notes})
            return {"status": "ok", "project_id": project_id, "action": "resume"}
        else:
            plugin_name = (project.get("plugin_name") or "?")
            logger.info(
                "[verify] project='%s' FRESH START  plugin=%s  has_state=%s",
                project_id, plugin_name, bool(state and state.values),
            )
            background_tasks.add_task(_run_start_bg, project_id, _build_initial_state(project))
            return {"status": "ok", "project_id": project_id, "action": "start"}

    if list_name == _COL_FAILED:
        try:
            graph = _get_graph(project_id)
            has_checkpoint = _has_checkpoint(graph, project_id)
        except Exception:
            has_checkpoint = False
        if has_checkpoint:
            notes = _get_latest_card_comment(card_id) if PLANKA_URL and PLANKA_TOKEN else ""
            background_tasks.add_task(_run_resume_bg, project_id, {"action": "terminate", "notes": notes})
            logger.info("Planka webhook: project=%s terminate active checkpoint", project_id)
        return {"status": "ok", "project_id": project_id, "action": "terminate"}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/llm")
async def health_llm():
    results = get_preflight_results()
    if not results:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"status": "not ready"})
    overall_ok = all(r.get("ok") for r in results.values())
    return {"ok": overall_ok, "results": results}


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
            started_at = (config or {}).get("review_started_at") or 0
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



# ---------------------------------------------------------------------------
# Planka helpers
# ---------------------------------------------------------------------------

def _extract_thread_id(description: str) -> str | None:
    if not description:
        return None
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
    """Move existing Planka card (identified by thread_id) to a named column.

    Strategy:
    1. If card_id is cached, fetch the card to get its boardId, then look up lists
       in that board — avoids hardcoded PLANKA_BOARD_ID mismatch.
    2. If no cache, fall back to scanning PLANKA_BOARD_ID (original behaviour).
    """
    if not (PLANKA_URL and PLANKA_TOKEN):
        return
    headers = {"Authorization": f"Bearer {PLANKA_TOKEN}"}
    try:
        card_id: str | None = None
        board_id: str | None = None

        # --- Step 1: resolve card_id from cache ---
        if _planka_sink:
            card_id = _planka_sink.resolve_card_id(project_id)

        # --- Step 2: resolve board_id from the card itself ---
        if card_id:
            card_resp = httpx.get(f"{PLANKA_URL}/api/cards/{card_id}", headers=headers, timeout=10)
            if card_resp.is_success:
                board_id = card_resp.json().get("item", {}).get("boardId")
            else:
                logger.warning(
                    "_move_planka_card: could not fetch card '%s' (status %s), clearing cache.",
                    card_id, card_resp.status_code,
                )
                if _planka_sink:
                    _planka_sink._cache.pop(project_id, None)
                card_id = None

        # --- Step 3: fall back to configured board scan if still no card ---
        if not card_id:
            board_id = PLANKA_BOARD_ID
            if not board_id:
                logger.warning("No Planka card found for project '%s' and PLANKA_BOARD_ID not set.", project_id)
                return
            board_resp = httpx.get(f"{PLANKA_URL}/api/boards/{board_id}", headers=headers, timeout=10)
            board_resp.raise_for_status()
            board_data = board_resp.json()
            for card in (board_data.get("included", {}).get("cards") or []):
                if _extract_thread_id(card.get("description") or "") == project_id:
                    card_id = card.get("id")
                    if _planka_sink:
                        _planka_sink.cache_card_id(project_id, card_id)
                    break

        if not card_id:
            logger.warning("No Planka card found for project '%s'.", project_id)
            return

        # --- Step 4: look up target list on the resolved board ---
        board_resp = httpx.get(f"{PLANKA_URL}/api/boards/{board_id}", headers=headers, timeout=10)
        board_resp.raise_for_status()
        lists = board_resp.json().get("included", {}).get("lists") or []
        target_list_id = next((l.get("id") for l in lists if l.get("name") == column_name), None)

        if not target_list_id:
            logger.warning("Planka column '%s' not found on board '%s'.", column_name, board_id)
            return

        # --- Step 5: move the card ---
        patch_resp = httpx.patch(
            f"{PLANKA_URL}/api/cards/{card_id}",
            headers=headers,
            json={"listId": target_list_id, "position": 65535},
            timeout=10,
        )
        if patch_resp.is_success:
            logger.info("Moved Planka card for project '%s' to '%s'.", project_id, column_name)
        else:
            logger.warning(
                "Planka PATCH card failed for project '%s': card_id=%s status=%s body=%s",
                project_id, card_id, patch_resp.status_code, patch_resp.text[:200],
            )
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
    from framework.llm_providers import LLMProviderFactory
    return LLMProviderFactory.build(provider)
