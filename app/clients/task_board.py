"""
app/clients/task_board.py

PlankaSink — central Planka HTTP client.

Responsibilities:
  - Resolve project_id → card_id (in-memory cache → DB → board scan)
  - Post comments to a card (POST /api/comment-actions)
  - Update a card's description (PATCH /api/cards/{id})

All methods are non-blocking: failures are logged as warnings, never raised.

Usage:
  sink = PlankaSink(url, token, board_id, db_url)
  sink.post_comment(project_id, "[PLAN] Loop 1\nstrategy: rsi_momentum")
  sink.update_card_description(project_id, "thread_id: proj-1\n\n---\n\n# Spec...")
"""

import json
import logging
import os
import re

import httpx

logger = logging.getLogger(__name__)


class PlankaClient:
    def __init__(self, url: str, token: str, board_id: str, db_url: str):
        self._url = url.rstrip("/")
        self._token = token
        self._board_id = board_id
        self._db_url = db_url  # framework business schema (projects table)
        self._cache: dict[str, str] = {}  # project_id → card_id
        self._session_cookies: dict[str, str] | None = None
        logger.info(
            "PlankaSink init: url=%r  board_id=%r  token_set=%s",
            self._url,
            self._board_id,
            bool(self._token),
        )
        self._check_connectivity()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve_card_id(self, project_id: str) -> str | None:
        """
        Resolve project_id → Planka card_id.
        Lookup order: in-memory cache → DB → full board scan.
        Result is persisted to cache + DB on first discovery.
        Returns None if not found; never raises.
        """
        # 1. In-memory cache
        if project_id in self._cache and self._cache[project_id]:
            return self._cache[project_id]

        # 2. DB lookup
        try:
            card_id = _get_planka_card_id_from_db(project_id, self._db_url)
            if card_id:
                self._cache[project_id] = card_id
                return card_id
        except Exception as e:
            logger.debug("DB card_id lookup failed for '%s': %s", project_id, e)

        # 3. Full board scan
        try:
            card_id = self._scan_board_for_card(project_id)
            if card_id:
                self.cache_card_id(project_id, card_id)
                return card_id
        except Exception as e:
            logger.warning("Board scan for card_id failed (project '%s'): %s", project_id, e)

        return None

    def post_comment(self, project_id: str, text: str) -> None:
        """
        Post a comment to the card associated with project_id.
        Uses POST /api/cards/{cardId}/comments {"text": ...}.
        Non-blocking: logs warning on failure.
        """
        card_id = self.resolve_card_id(project_id)
        if not card_id:
            logger.warning("post_comment: no card found for project '%s', skipping.", project_id)
            return
        url = f"{self._url}/api/cards/{card_id}/comments"
        logger.info("post_comment → POST %s  text_preview=%r", url, text[:80])
        try:
            resp = httpx.post(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"text": text},
                timeout=10,
            )
            resp.raise_for_status()
            logger.info("post_comment: OK  project='%s'", project_id)
        except Exception as e:
            logger.warning(
                "Planka post_comment failed (project '%s'): url=%r  %s(%s)",
                project_id, url, type(e).__name__, e,
            )

    def update_card_description(self, project_id: str, description: str) -> None:
        """
        Update the Planka card description.
        Uses PATCH /api/cards/{card_id} {"description": ...}.
        Non-blocking: logs warning on failure.
        """
        card_id = self.resolve_card_id(project_id)
        if not card_id:
            logger.debug("update_card_description: no card found for project '%s', skipping.", project_id)
            return
        url = f"{self._url}/api/cards/{card_id}"
        logger.info("update_card_description → PATCH %s  project='%s'", url, project_id)
        try:
            resp = httpx.patch(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={"description": description},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning(
                "Planka update_card_description failed (project '%s'): url=%r  %s(%s)",
                project_id, url, type(e).__name__, e,
            )

    def cache_card_id(self, project_id: str, card_id: str) -> None:
        """Persist card_id to in-memory cache and DB."""
        self._cache[project_id] = card_id
        try:
            _set_planka_card_id_in_db(project_id, card_id, self._db_url)
        except Exception as e:
            logger.debug("DB card_id persist failed for '%s': %s", project_id, e)

    def download_latest_spec_attachment(self, card_id: str) -> str | None:
        """
        Download the most recently uploaded .md attachment from a card and save it locally.

        Uses GET /api/cards/{cardId} → included.attachments, picks latest by createdAt.
        Primary path: MinIO direct download (looks up uploadedFileId from Planka DB).
        Fallback: Planka /attachments/{id}/download endpoint with session-cookie auth.
        Saves the file to ${VOLUME_BASE_DIR}/{timestamp}/<filename> and returns the local path.
        Returns None if no .md attachment found or on any error.
        Non-blocking: returns None on any error.
        """
        url = f"{self._url}/api/cards/{card_id}"
        logger.info("download_latest_spec_attachment → GET %s", url)
        try:
            resp = httpx.get(
                url,
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10,
            )
            resp.raise_for_status()
            attachments = resp.json().get("included", {}).get("attachments") or []
            md_attachments = [
                a for a in attachments
                if (a.get("name") or "").lower().endswith(".md")
                and not (a.get("name") or "").lower().startswith("reviewed_spec")
            ]
            if not md_attachments:
                logger.debug(
                    "download_latest_spec_attachment: no .md attachments on card '%s' (total attachments: %d)",
                    card_id, len(attachments),
                )
                return None
            # Sort by createdAt descending, pick latest
            latest = sorted(
                md_attachments,
                key=lambda a: a.get("createdAt") or "",
                reverse=True,
            )[0]
            att_id = latest.get("id", "")
            att_name = latest.get("name", "spec.md")
            logger.info(
                "download_latest_spec_attachment: found '%s' (id=%s) from %d total attachments",
                att_name, att_id, len(attachments),
            )
            save_path = _make_volume_path(att_name)
            if save_path is None:
                return None
            # Try MinIO direct download first (works when Planka uses S3 storage)
            result = self._download_via_minio(att_id, att_name, save_path)
            if result:
                return result
            # Fallback: Planka HTTP download (works when Planka uses local storage)
            return self._download_attachment_http(att_id, att_name, save_path)
        except Exception as e:
            logger.warning(
                "download_latest_spec_attachment failed for card '%s': url=%r  %s(%s)",
                card_id, url, type(e).__name__, e,
            )
            return None

    def get_card_comments(self, card_id: str) -> list[dict]:
        """
        Fetch all comments for a card, sorted by createdAt ascending.

        Uses GET /api/cards/{cardId}/actions, filters type=='commentCard'.
        Returns list of {"text": str, "createdAt": str}.
        Returns [] on error (non-blocking).
        """
        try:
            resp = httpx.get(
                f"{self._url}/api/cards/{card_id}/actions",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            comments = [
                {"text": item["data"]["text"], "createdAt": item.get("createdAt", "")}
                for item in items
                if item.get("type") == "commentCard"
            ]
            return sorted(comments, key=lambda c: c["createdAt"])
        except Exception as e:
            logger.warning("get_card_comments failed for card '%s': %s", card_id, e)
            return []

    def upload_spec_attachment(self, card_id: str, filename: str, content: str) -> None:
        """
        Upload a markdown file as a card attachment.

        Uses POST /api/cards/{cardId}/attachments multipart.
        Non-blocking: logs warning on failure.
        """
        try:
            import io
            file_bytes = content.encode("utf-8")
            resp = httpx.post(
                f"{self._url}/api/cards/{card_id}/attachments",
                headers={"Authorization": f"Bearer {self._token}"},
                data={"type": "file", "name": filename},
                files={"file": (filename, io.BytesIO(file_bytes), "text/markdown")},
                timeout=30,
            )
            resp.raise_for_status()
            logger.info("upload_spec_attachment: OK  filename='%s' card_id=%s", filename, card_id)
        except Exception as e:
            logger.warning("upload_spec_attachment failed for card '%s': %s", card_id, e)

    def upload_bytes_attachment(
        self, card_id: str, filename: str, data: bytes, mime: str = "application/octet-stream"
    ) -> None:
        """Upload a binary file as a card attachment."""
        try:
            import io
            resp = httpx.post(
                f"{self._url}/api/cards/{card_id}/attachments",
                headers={"Authorization": f"Bearer {self._token}"},
                data={"type": "file", "name": filename},
                files={"file": (filename, io.BytesIO(data), mime)},
                timeout=60,
            )
            resp.raise_for_status()
            logger.info("upload_bytes_attachment: OK  filename='%s' card_id=%s", filename, card_id)
        except Exception as e:
            logger.warning("upload_bytes_attachment failed for card '%s': %s", card_id, e)

    def read_card_custom_fields(self, card_id: str) -> dict:
        """
        Read custom field values for a specific card.

        Returns dict like {"max_loops": 3}.
        Reads from GET /api/boards/{boardId} customFields + customFieldItems.
        """
        try:
            resp = httpx.get(
                f"{self._url}/api/boards/{self._board_id}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10,
            )
            resp.raise_for_status()
            included = resp.json().get("included", {})
            custom_fields = {
                cf["id"]: cf["name"]
                for cf in (included.get("customFields") or [])
            }
            field_items = [
                fi for fi in (included.get("customFieldItems") or [])
                if fi.get("cardId") == card_id
            ]
            return {
                custom_fields[fi["customFieldId"]]: fi.get("value")
                for fi in field_items
                if fi["customFieldId"] in custom_fields
            }
        except Exception as e:
            logger.warning("read_card_custom_fields failed: %s", e)
            return {}

    def ensure_custom_fields(self) -> None:
        """
        Idempotently create required custom fields on the board.

        Creates group 'Research Config' then field: max_loops.
        Called once at startup; safe to call repeatedly.
        """
        required = ["max_loops"]
        group_name = "Research Config"
        try:
            resp = httpx.get(
                f"{self._url}/api/boards/{self._board_id}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10,
            )
            resp.raise_for_status()
            included = resp.json().get("included", {})
            existing_groups = included.get("customFieldGroups") or []
            existing_fields = {cf["name"] for cf in (included.get("customFields") or [])}

            # Ensure group exists
            group_id = next(
                (g["id"] for g in existing_groups if g["name"] == group_name), None
            )
            if not group_id:
                r = httpx.post(
                    f"{self._url}/api/boards/{self._board_id}/custom-field-groups",
                    headers={"Authorization": f"Bearer {self._token}"},
                    json={"name": group_name, "position": 1},
                    timeout=10,
                )
                r.raise_for_status()
                group_id = r.json()["item"]["id"]
                logger.info("Created Planka custom field group '%s'.", group_name)

            # Ensure each field exists
            for position, name in enumerate(required, start=1):
                if name not in existing_fields:
                    r = httpx.post(
                        f"{self._url}/api/custom-field-groups/{group_id}/custom-fields",
                        headers={"Authorization": f"Bearer {self._token}"},
                        json={"name": name, "position": position},
                        timeout=10,
                    )
                    if r.status_code in (200, 201):
                        logger.info("Created Planka custom field '%s'.", name)
                    else:
                        logger.warning(
                            "Failed to create custom field '%s': %s", name, r.text[:200]
                        )
                else:
                    logger.debug("Planka custom field '%s' already exists.", name)
        except Exception as e:
            logger.warning("ensure_custom_fields failed: %s", e)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_session_cookies(self) -> dict[str, str]:
        """
        Login to Planka and cache session cookies.

        NOTE: Newer Planka versions (with S3 storage) no longer set cookies on
        POST /api/access-tokens, returning only a JWT in the body. In that case
        this method returns {} and _download_attachment_http will fail.
        Use _download_via_minio instead (called automatically by
        download_latest_spec_attachment as the primary path).

        Returns {} if creds are missing, login fails, or Planka sets no cookies.
        """
        if self._session_cookies is not None:
            return self._session_cookies
        email = (
            os.getenv("PLANKA_LOGIN_USERNAME")
            or os.getenv("DEFAULT_ADMIN_EMAIL")
            or os.getenv("DEFAULT_ADMIN_USERNAME")
            or ""
        )
        password = (
            os.getenv("PLANKA_LOGIN_PASSWORD")
            or os.getenv("DEFAULT_ADMIN_PASSWORD")
            or ""
        )
        if not (email and password):
            logger.warning("Planka login skipped: no email/password env vars set.")
            self._session_cookies = {}
            return self._session_cookies
        try:
            resp = httpx.post(
                f"{self._url}/api/access-tokens",
                json={"emailOrUsername": email, "password": password},
                timeout=10,
            )
            resp.raise_for_status()
            self._session_cookies = dict(resp.cookies)
            logger.info(
                "Planka session login OK: cookies=%s", list(self._session_cookies.keys()),
            )
        except Exception as e:
            logger.warning("Planka session login failed: %s", e)
            self._session_cookies = {}
        return self._session_cookies

    def _download_via_minio(
        self, att_id: str, att_name: str, save_path: str,
    ) -> str | None:
        """
        Download an attachment directly from MinIO by looking up the uploadedFileId
        from the Planka DB.

        Planka stores attachment files under private/attachments/{uploadedFileId}/{filename}
        in the planka-attachments bucket. The uploadedFileId differs from the API-level
        attachment id, so we need a DB lookup.
        Returns the local save_path on success, None on any failure.
        """
        try:
            uploaded_file_id = _get_uploadedfile_id_from_planka_db(att_id, self._db_url)
            if not uploaded_file_id:
                logger.debug("_download_via_minio: no uploadedFileId for att_id=%s", att_id)
                return None
            object_key = f"private/attachments/{uploaded_file_id}/{att_name}"
            endpoint = os.getenv("MINIO_ENDPOINT", "")
            access_key = os.getenv("MINIO_ROOT_USER", "") or os.getenv("MINIO_ACCESS_KEY", "")
            secret_key = os.getenv("MINIO_ROOT_PASSWORD", "") or os.getenv("MINIO_SECRET_KEY", "")
            if not (endpoint and access_key and secret_key):
                logger.debug("_download_via_minio: MinIO env vars not set, skipping")
                return None
            from minio import Minio
            secure = os.getenv("MINIO_SECURE", "false").lower() == "true"
            client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)
            bucket = os.getenv("MINIO_PLANKA_BUCKET", "planka-attachments")
            response = client.get_object(bucket, object_key)
            data = response.read()
            response.close()
            with open(save_path, "wb") as f:
                f.write(data)
            logger.info(
                "_download_via_minio: saved '%s' (%d bytes) key=%s → %s",
                att_name, len(data), object_key, save_path,
            )
            return save_path
        except Exception as e:
            logger.warning(
                "_download_via_minio failed (att_id=%s name=%r): %s(%s)",
                att_id, att_name, type(e).__name__, e,
            )
            return None

    def _download_attachment_http(
        self, att_id: str, att_name: str, save_path: str,
    ) -> str | None:
        """
        Download an attachment via Planka's HTTP API using session-cookie auth.

        Endpoint: GET {url}/attachments/{id}/download/{filename}
        Saves bytes to save_path and returns it. Returns None on any failure.
        """
        cookies = self._get_session_cookies()
        if not cookies:
            return None
        url = f"{self._url}/attachments/{att_id}/download/{att_name}"
        try:
            with httpx.Client(
                cookies=cookies, follow_redirects=True, timeout=30,
            ) as client:
                resp = client.get(url)
                if resp.status_code in (401, 403):
                    # Cookie may have expired — drop cache and retry once.
                    self._session_cookies = None
                    cookies = self._get_session_cookies()
                    if not cookies:
                        return None
                    resp = httpx.get(url, cookies=cookies, follow_redirects=True, timeout=30)
                resp.raise_for_status()
                with open(save_path, "wb") as f:
                    f.write(resp.content)
            logger.info(
                "download attachment: saved '%s' (%d bytes) → %s",
                att_name, len(resp.content), save_path,
            )
            return save_path
        except Exception as e:
            logger.warning(
                "Planka attachment download failed (id=%s name=%r): %s(%s)",
                att_id, att_name, type(e).__name__, e,
            )
            return None

    def _check_connectivity(self) -> None:
        """Probe Planka at startup and log the result. Never raises."""
        try:
            resp = httpx.get(f"{self._url}/api/health", timeout=5)
            logger.info("Planka connectivity check: url=%r status=%s", self._url, resp.status_code)
        except Exception as e:
            logger.warning(
                "Planka connectivity check FAILED: url=%r  error=%s(%s)",
                self._url,
                type(e).__name__,
                e,
            )

    def _scan_board_for_card(self, project_id: str) -> str | None:
        """Fetch full board data and find the card whose description contains thread_id: <project_id>."""
        resp = httpx.get(
            f"{self._url}/api/boards/{self._board_id}",
            headers={"Authorization": f"Bearer {self._token}"},
            timeout=10,
        )
        resp.raise_for_status()
        cards = resp.json().get("included", {}).get("cards") or []
        for card in cards:
            if _extract_thread_id(card.get("description") or "") == project_id:
                return card.get("id")
        return None


# ------------------------------------------------------------------
# DB helpers (thin wrappers to avoid circular imports with queries.py)
# ------------------------------------------------------------------

def _get_planka_card_id_from_db(project_id: str, db_url: str | None) -> str | None:
    from app.db.queries import get_project
    row = get_project(project_id, db_url)
    return (row.get("config") or {}).get("planka_card_id") if row else None


def _set_planka_card_id_in_db(project_id: str, card_id: str, db_url: str | None) -> None:
    from app.db.connection import get_connection
    with get_connection(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE projects SET config = config || %s::jsonb WHERE id = %s",
                (json.dumps({"planka_card_id": card_id}), project_id),
            )


def _get_uploadedfile_id_from_planka_db(att_id: str, db_url: str | None) -> str | None:
    """
    Look up the uploadedFileId for a Planka attachment from the Planka DB.

    Planka stores attachment files in S3 under private/attachments/{uploadedFileId}/,
    not the attachment's API id. The uploadedFileId lives in attachment.data->>'uploadedFileId'.
    Derives Planka DB URL from db_url by replacing the DB name with PLANKA_POSTGRES_DB env var.
    Returns None on any failure.
    """
    if not db_url:
        return None
    try:
        planka_db_name = os.getenv("PLANKA_POSTGRES_DB", "agentic-research-planka")
        planka_db_url = re.sub(r"/[^/]+$", f"/{planka_db_name}", db_url)
        import psycopg
        with psycopg.connect(planka_db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT data->>'uploadedFileId' FROM attachment WHERE id = %s",
                    (att_id,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.debug("_get_uploadedfile_id_from_planka_db att_id=%s: %s(%s)", att_id, type(e).__name__, e)
        return None


def _extract_thread_id(description: str) -> str | None:
    if not description:
        return None
    match = re.search(r"thread_id:\s*(\S+)", description)
    return match.group(1) if match else None


def _make_volume_path(filename: str) -> str | None:
    """
    Build ${VOLUME_BASE_DIR}/agentic-framework-api/llm/{yyyyMMddHHmmSS.S}/{filename},
    create the directory, and return the path.
    Returns None on failure.
    """
    import os
    from datetime import datetime
    volume_base = os.getenv("VOLUME_BASE_DIR", "./data")
    ts = datetime.now().strftime("%Y%m%d%H%M%S.%f")[:-4]  # yyyyMMddHHmmSS.S  (e.g. 20260327143022.1)
    save_dir = os.path.join(volume_base, "agentic-framework-api", "llm", ts)
    try:
        os.makedirs(save_dir, exist_ok=True)
        return os.path.join(save_dir, filename)
    except Exception as e:
        logger.warning("_make_volume_path failed for '%s': %s", filename, e)
        return None


