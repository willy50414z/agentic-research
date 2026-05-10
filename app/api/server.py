"""
app/api/server.py

FastAPI server for the agentic research framework.

Planka column state machine:
  Planning → Spec Pending Review → Executing → Review → Done / Failed

Endpoints:
  POST /planka-webhook  — Planka card-move events
  POST /init-planka-board — one-shot board initialisation
  GET  /health
  GET  /health/llm

Webhook routing:
  Spec Pending Review → run_spec_review_step  (llm_eval spec agent)
  Executing           → dispatch_step          (step-based workflow)

End conditions:
  last_result == "PASS"      → card moves to Done
  last_result == "TERMINATE" → card moves to Review

Scheduler:
  Every 60 s: clear stale review_in_progress flags (crash recovery).
"""

import asyncio
import logging
import os
import re
import time
from threading import Lock

import httpx
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, BackgroundTasks

from app.db.queries import create_project, get_project, merge_config
from llm_eval.preflight import check_target
from app.llm import get_all_configured_targets

from app.workflow.executing_step import dispatch_step
from app.workflow.spec_review_step import run_spec_review_step

logger = logging.getLogger(__name__)

DATABASE_URL    = os.getenv("DATABASE_URL", "")
PLANKA_URL      = os.getenv("PLANKA_API_URL", "")
PLANKA_TOKEN    = os.getenv("PLANKA_TOKEN", "")
PLANKA_BOARD_ID = os.getenv("PLANKA_BOARD_ID", "")

_REVIEW_STALE_TIMEOUT: int = int(os.getenv("REVIEW_STALE_TIMEOUT", "2400"))

_COL_PLANNING     = "Planning"
_COL_SPEC_PENDING = "Spec Pending Review"
_COL_EXECUTING    = "Executing"
_COL_REVIEW       = "Review"
_COL_DONE         = "Done"
_COL_FAILED       = "Failed"


# ---------------------------------------------------------------------------
# AppServices
# ---------------------------------------------------------------------------

class AppServices:
    def __init__(
        self,
        db_url: str,
        planka_url: str,
        planka_token: str,
        planka_board_id: str,
    ):
        self._db_url = db_url
        self._planka_url = planka_url.rstrip("/") if planka_url else ""
        self._planka_token = planka_token
        self._planka_board_id = planka_board_id
        self._running: set[str] = set()
        self._running_lock = Lock()

        self._planka_client = None
        if planka_url and planka_token and planka_board_id:
            from app.clients.task_board import PlankaClient
            self._planka_client = PlankaClient(planka_url, planka_token, planka_board_id, db_url)
            self._planka_client.ensure_custom_fields()
            logger.info("PlankaSink initialized.")

    # ------------------------------------------------------------------
    # Background task runners
    # ------------------------------------------------------------------

    def run_dispatch_bg(self, project_id: str, card_id: str = "") -> None:
        with self._running_lock:
            if project_id in self._running:
                logger.warning("[dispatch_bg] SKIP — already running  project='%s'", project_id)
                return
            self._running.add(project_id)
        try:
            card_raw_max_loops = None
            merged_max_loops = None
            if card_id and self._planka_client:
                try:
                    fields = self._planka_client.read_card_custom_fields(card_id)
                    raw = fields.get("max_loops")
                    card_raw_max_loops = raw
                    if raw is not None and str(raw).strip():
                        parsed = int(str(raw).strip())
                        merge_config(project_id, {"max_loops": parsed}, self._db_url)
                        # Re-read project to confirm merge_config write landed before dispatch_step
                        project_after = get_project(project_id, self._db_url)
                        merged_max_loops = ((project_after or {}).get("config") or {}).get("max_loops")
                        logger.info(
                            "[dispatch_bg] max_loops sync: card_raw=%s merged=%s  project='%s'",
                            raw, merged_max_loops, project_id,
                        )
                        if merged_max_loops != parsed:
                            logger.warning(
                                "[dispatch_bg] max_loops mismatch: card_raw=%s merged=%s  project='%s'",
                                raw, merged_max_loops, project_id,
                            )
                    else:
                        logger.info(
                            "[dispatch_bg] max_loops sync: card_raw=null (using DB / default)  project='%s'",
                            project_id,
                        )
                except (ValueError, TypeError) as e:
                    logger.warning("[dispatch_bg] max_loops sync failed (invalid value): %s", e)
                except Exception as e:
                    logger.warning("[dispatch_bg] max_loops sync failed: %s", e)
            dispatch_step(project_id, db_url=self._db_url, sink=self._planka_client, move_card_fn=self.move_card)
        finally:
            with self._running_lock:
                self._running.discard(project_id)

    def run_spec_review_bg(
        self,
        project_id: str,
        card_id: str,
        card_name: str,
        description: str,
    ) -> None:
        run_spec_review_step(
            project_id=project_id,
            card_id=card_id,
            card_name=card_name,
            description=description,
            db_url=self._db_url,
            planka_client=self._planka_client,
            move_card_fn=self.move_card,
        )

    def is_running(self, project_id: str) -> bool:
        with self._running_lock:
            return project_id in self._running

    # ------------------------------------------------------------------
    # Planka card movement
    # ------------------------------------------------------------------

    def move_card(self, project_id: str, column_name: str) -> None:
        if not (self._planka_url and self._planka_token):
            return
        headers = {"Authorization": f"Bearer {self._planka_token}"}
        try:
            card_id: str | None = None
            board_id: str | None = None

            if self._planka_client:
                card_id = self._planka_client.resolve_card_id(project_id)

            if card_id:
                card_resp = httpx.get(
                    f"{self._planka_url}/api/cards/{card_id}", headers=headers, timeout=10,
                )
                if card_resp.is_success:
                    board_id = card_resp.json().get("item", {}).get("boardId")
                else:
                    if self._planka_client:
                        self._planka_client._cache.pop(project_id, None)
                    card_id = None

            if not card_id:
                board_id = self._planka_board_id
                if not board_id:
                    return
                board_resp = httpx.get(
                    f"{self._planka_url}/api/boards/{board_id}", headers=headers, timeout=10,
                )
                board_resp.raise_for_status()
                for card in (board_resp.json().get("included", {}).get("cards") or []):
                    if _extract_thread_id(card.get("description") or "") == project_id:
                        card_id = card.get("id")
                        if self._planka_client:
                            self._planka_client.cache_card_id(project_id, card_id)
                        break

            if not card_id:
                logger.warning("No Planka card found for project '%s'.", project_id)
                return

            board_resp = httpx.get(
                f"{self._planka_url}/api/boards/{board_id}", headers=headers, timeout=10,
            )
            board_resp.raise_for_status()
            lists = board_resp.json().get("included", {}).get("lists") or []
            target_list_id = next(
                (lst.get("id") for lst in lists if lst.get("name") == column_name), None,
            )
            if not target_list_id:
                logger.warning("Planka column '%s' not found.", column_name)
                return

            patch_resp = httpx.patch(
                f"{self._planka_url}/api/cards/{card_id}",
                headers=headers,
                json={"listId": target_list_id, "position": 65535},
                timeout=10,
            )
            if patch_resp.is_success:
                logger.info("Moved card for project '%s' to '%s'.", project_id, column_name)
            else:
                logger.warning(
                    "PATCH card failed: %s %s", patch_resp.status_code, patch_resp.text[:200],
                )
        except Exception as e:
            logger.warning("move_card error for '%s': %s", project_id, e)

    # ------------------------------------------------------------------
    # Stale review recovery (called by scheduler)
    # ------------------------------------------------------------------

    async def scan_stalled_reviews(self) -> None:
        if not self._db_url:
            return
        try:
            from app.db.connection import get_connection
            from app.db.queries import merge_config
            stale_cutoff = time.time() - _REVIEW_STALE_TIMEOUT
            with get_connection(self._db_url) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id, config FROM projects"
                        " WHERE (config->>'review_in_progress')::boolean = true"
                    )
                    rows = cur.fetchall()
            for project_id, config in rows:
                started_at = (config or {}).get("review_started_at") or 0
                if started_at < stale_cutoff:
                    logger.warning("Clearing stalled review for project '%s'.", project_id)
                    merge_config(project_id, {"review_in_progress": False}, self._db_url)
                    if self._planka_client:
                        self._planka_client.post_comment(
                            project_id,
                            "Spec review timed out — move card back to Planning and try again.",
                        )
                    self.move_card(project_id, _COL_PLANNING)
        except Exception as e:
            logger.warning("scan_stalled_reviews error: %s", e)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(app: FastAPI):
    _ensure_planka_columns()
    app.state.svc = AppServices(DATABASE_URL, PLANKA_URL, PLANKA_TOKEN, PLANKA_BOARD_ID)
    _run_preflight()
    task = asyncio.create_task(_scheduler_loop(app.state.svc))
    logger.info("Scheduler started.")
    yield
    task.cancel()


app = FastAPI(title="Agentic Research API", version="0.5.0", lifespan=_lifespan)


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------

def _run_preflight() -> None:
    errors = []
    for target in get_all_configured_targets():
        try:
            status = check_target(target)
            if status.ok:
                logger.info("preflight: %s OK", target.value)
            else:
                errors.append(f"llm ({target.value}): {status.reason}")
        except ValueError as e:
            errors.append(str(e))

    try:
        import psycopg
        with psycopg.connect(DATABASE_URL, autocommit=True) as conn:
            conn.execute("SELECT 1")
        logger.info("preflight: database OK")
    except Exception as e:
        errors.append(f"database: {e}")

    if errors:
        raise RuntimeError(f"Preflight failed — {'; '.join(errors)}")


def get_preflight_results() -> dict:
    entries = []
    for target in get_all_configured_targets():
        try:
            status = check_target(target)
            entry: dict = {"target": target.value, "ok": status.ok}
            if status.reason:
                entry["reason"] = status.reason
        except ValueError as e:
            entry = {"target": target.value, "ok": False, "reason": str(e)}
        entries.append(entry)
    return {"llm_targets": entries}


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

async def _scheduler_loop(svc: AppServices) -> None:
    while True:
        await asyncio.sleep(60)
        try:
            await svc.scan_stalled_reviews()
        except Exception as e:
            logger.warning("Scheduler error: %s", e)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/planka-webhook")
async def planka_webhook(request: Request, background_tasks: BackgroundTasks):
    svc: AppServices = request.app.state.svc
    payload = await request.json()
    event = payload.get("event", "")
    logger.info("[WEBHOOK] event=%s", event)

    if event != "cardUpdate":
        return {"status": "ignored", "event": event}

    data        = payload.get("data") or {}
    card        = data.get("item") or {}
    card_id     = card.get("id", "")
    description = card.get("description") or ""
    card_name   = card.get("name", "")

    included        = data.get("included") or {}
    lists_included  = included.get("lists") or []
    current_list_id = card.get("listId", "")
    list_name = next(
        (lst.get("name", "") for lst in lists_included if lst.get("id") == current_list_id),
        "",
    )

    prev_data    = payload.get("prevData") or {}
    prev_list_id = (prev_data.get("item") or {}).get("listId", "")
    if not list_name or current_list_id == prev_list_id:
        return {"status": "ignored", "reason": "not a list change"}

    logger.info("webhook: card=%r list=%r", card_name, list_name)

    if list_name == _COL_SPEC_PENDING:
        project_id = _extract_thread_id(description) or _slugify(card_name) or card_id
        background_tasks.add_task(svc.run_spec_review_bg, project_id, card_id, card_name, description)
        return {"status": "spec_review_queued", "project_id": project_id}

    if list_name == _COL_EXECUTING:
        project_id = _extract_thread_id(description)
        if not project_id:
            logger.warning("webhook: no thread_id in card '%s'", card_name)
            return {"status": "error", "detail": "thread_id not found in card description"}

        project = get_project(project_id)
        if project is None:
            logger.warning("webhook: project '%s' not found", project_id)
            return {"status": "error", "detail": f"Project '{project_id}' not found."}

        if svc.is_running(project_id):
            logger.warning("webhook: project '%s' already running — skipped", project_id)
            return {"status": "skipped", "reason": "already_running"}

        background_tasks.add_task(svc.run_dispatch_bg, project_id, card_id)
        return {"status": "ok", "project_id": project_id, "action": "dispatch"}

    logger.debug("webhook: ignoring list '%s'", list_name)
    return {"status": "ignored", "list": list_name}


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/llm")
async def health_llm():
    results = get_preflight_results()
    overall_ok = all(r.get("ok") for r in results["llm_targets"])
    if not overall_ok:
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=503, content={"ok": False, "results": results})
    return {"ok": True, "results": results}


# ---------------------------------------------------------------------------
# Board initialisation
# ---------------------------------------------------------------------------

@app.post("/init-planka-board")
async def init_planka_board(request: Request):
    """
    Initialise a Planka project + board + lists + custom fields + webhook.

    Body (JSON): base_url, email, password, project_name, board_name, webhook_url
    Returns: token, board_id, log
    """
    body = await request.json()
    base_url     = (body.get("base_url") or "http://localhost:7002").rstrip("/")
    email        = body.get("email", "")
    password     = body.get("password", "")
    project_name = body.get("project_name", "Agentic Research")
    board_name   = body.get("board_name", "Research Workflow")
    webhook_url  = body.get("webhook_url", "http://agentic-framework-api:8000/planka-webhook")

    log = []
    resp = httpx.post(
        f"{base_url}/api/access-tokens",
        json={"emailOrUsername": email, "password": password},
        timeout=10,
    )
    resp.raise_for_status()
    token = resp.json()["item"]
    headers = {"Authorization": f"Bearer {token}"}
    log.append("✓ Logged in")

    resp = httpx.get(f"{base_url}/api/projects", headers=headers, timeout=10)
    resp.raise_for_status()
    projects = resp.json().get("items", [])
    personal = next((p for p in projects if p.get("type") in ("private", None)), None)
    if personal:
        project_id = personal["id"]
        log.append(f"✓ Using existing personal project '{personal.get('name')}'")
    else:
        r = httpx.post(
            f"{base_url}/api/projects", headers=headers,
            json={"name": project_name, "type": "private"}, timeout=10,
        )
        r.raise_for_status()
        project_id = r.json()["item"]["id"]
        log.append(f"✓ Project '{project_name}' created")

    resp = httpx.post(
        f"{base_url}/api/projects/{project_id}/boards", headers=headers,
        json={"name": board_name, "position": 1}, timeout=10,
    )
    resp.raise_for_status()
    board_id = resp.json()["item"]["id"]
    log.append(f"✓ Board '{board_name}' created  (id: {board_id})")

    _LISTS = [
        (_COL_PLANNING,     10000),
        (_COL_SPEC_PENDING, 20000),
        (_COL_EXECUTING,    25000),
        (_COL_REVIEW,       30000),
        (_COL_DONE,         40000),
        (_COL_FAILED,       50000),
    ]
    resp = httpx.get(f"{base_url}/api/boards/{board_id}", headers=headers, timeout=10)
    resp.raise_for_status()
    existing_lists = {lst["name"] for lst in (resp.json().get("included", {}).get("lists") or [])}
    for name, position in _LISTS:
        if name in existing_lists:
            log.append(f"– List '{name}' already exists")
            continue
        r = httpx.post(
            f"{base_url}/api/boards/{board_id}/lists", headers=headers,
            json={"name": name, "position": position, "type": "active"}, timeout=10,
        )
        r.raise_for_status()
        log.append(f"✓ List '{name}' created")

    resp = httpx.get(f"{base_url}/api/boards/{board_id}", headers=headers, timeout=10)
    resp.raise_for_status()
    included       = resp.json().get("included", {})
    existing_groups = included.get("customFieldGroups") or []
    existing_fields = {cf["name"] for cf in (included.get("customFields") or [])}
    group_name = "Research Config"
    group_id = next((g["id"] for g in existing_groups if g["name"] == group_name), None)
    if not group_id:
        r = httpx.post(
            f"{base_url}/api/boards/{board_id}/custom-field-groups", headers=headers,
            json={"name": group_name, "position": 1}, timeout=10,
        )
        r.raise_for_status()
        group_id = r.json()["item"]["id"]
        log.append(f"✓ Custom field group '{group_name}' created")
    if "max_loops" not in existing_fields:
        r = httpx.post(
            f"{base_url}/api/custom-field-groups/{group_id}/custom-fields", headers=headers,
            json={"name": "max_loops", "position": 1}, timeout=10,
        )
        r.raise_for_status()
        log.append("✓ Custom field 'max_loops' created")

    resp = httpx.get(f"{base_url}/api/webhooks", headers=headers, timeout=10)
    existing_webhooks = resp.json().get("items", []) if resp.status_code == 200 else []
    if not any(w.get("url") == webhook_url for w in existing_webhooks):
        r = httpx.post(
            f"{base_url}/api/webhooks", headers=headers,
            json={"name": "agentic-research", "url": webhook_url, "events": "cardUpdate"},
            timeout=10,
        )
        if r.status_code in (200, 201):
            log.append(f"✓ Webhook created → {webhook_url}")
        else:
            log.append(f"✗ Webhook creation failed — set manually: {webhook_url}")

    env_instructions = f"Update .env:\n  PLANKA_TOKEN={token}\n  PLANKA_BOARD_ID={board_id}"
    logger.info("[init-planka] DONE — %s", env_instructions)
    return {
        "status": "ok", "token": token, "board_id": board_id,
        "log": log, "env_update_required": env_instructions,
    }


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def _extract_thread_id(description: str) -> str | None:
    if not description:
        return None
    match = re.search(r"thread_id:\s*(\S+)", description)
    return match.group(1) if match else None


def _slugify(name: str) -> str:
    import unicodedata
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:60]


def _ensure_planka_columns() -> None:
    if not (PLANKA_URL and PLANKA_TOKEN and PLANKA_BOARD_ID):
        return
    required = [
        (_COL_PLANNING,     10000),
        (_COL_SPEC_PENDING, 20000),
        (_COL_EXECUTING,    25000),
        (_COL_REVIEW,       30000),
        (_COL_DONE,         40000),
        (_COL_FAILED,       50000),
    ]
    try:
        headers = {"Authorization": f"Bearer {PLANKA_TOKEN}"}
        resp = httpx.get(
            f"{PLANKA_URL}/api/boards/{PLANKA_BOARD_ID}", headers=headers, timeout=10,
        )
        resp.raise_for_status()
        existing = {lst["name"] for lst in (resp.json().get("included", {}).get("lists") or [])}
        for name, position in required:
            if name not in existing:
                r = httpx.post(
                    f"{PLANKA_URL}/api/boards/{PLANKA_BOARD_ID}/lists", headers=headers,
                    json={"name": name, "position": position, "type": "active"}, timeout=10,
                )
                if r.status_code == 200:
                    logger.info("Created Planka column '%s'.", name)
    except Exception as e:
        logger.warning("_ensure_planka_columns error: %s", e)
