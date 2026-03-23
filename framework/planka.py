"""
framework/planka.py

PlankaSink — central Planka HTTP client for the framework.

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
import re

import httpx

logger = logging.getLogger(__name__)


class PlankaSink:
    def __init__(self, url: str, token: str, board_id: str, db_url: str):
        self._url = url.rstrip("/")
        self._token = token
        self._board_id = board_id
        self._db_url = db_url
        self._cache: dict[str, str] = {}  # project_id → card_id

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
        if project_id in self._cache:
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
            logger.debug("post_comment: no card found for project '%s', skipping.", project_id)
            return
        try:
            resp = httpx.post(
                f"{self._url}/api/cards/{card_id}/comments",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"text": text},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Planka post_comment failed (project '%s'): %s", project_id, e)

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
        try:
            resp = httpx.patch(
                f"{self._url}/api/cards/{card_id}",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"description": description},
                timeout=10,
            )
            resp.raise_for_status()
        except Exception as e:
            logger.warning("Planka update_card_description failed (project '%s'): %s", project_id, e)

    def cache_card_id(self, project_id: str, card_id: str) -> None:
        """Persist card_id to in-memory cache and DB."""
        self._cache[project_id] = card_id
        try:
            _set_planka_card_id_in_db(project_id, card_id, self._db_url)
        except Exception as e:
            logger.debug("DB card_id persist failed for '%s': %s", project_id, e)

    def download_latest_spec_attachment(self, card_id: str) -> str | None:
        """
        Download the most recently uploaded .md attachment from a card.

        Uses GET /api/cards/{cardId} → included.attachments, picks latest by createdAt.
        Downloads via MinIO (Planka's /attachments/ endpoint doesn't accept Bearer token
        auth — it only uses cookie sessions; MinIO gives direct S3 access).
        Returns the attachment text content, or None if no .md attachment found.
        Non-blocking: returns None on any error.
        """
        try:
            resp = httpx.get(
                f"{self._url}/api/cards/{card_id}",
                headers={"Authorization": f"Bearer {self._token}"},
                timeout=10,
            )
            resp.raise_for_status()
            attachments = resp.json().get("included", {}).get("attachments") or []
            md_attachments = [
                a for a in attachments
                if (a.get("name") or "").lower().endswith(".md")
            ]
            if not md_attachments:
                return None
            # Sort by createdAt descending, pick latest
            latest = sorted(
                md_attachments,
                key=lambda a: a.get("createdAt") or "",
                reverse=True,
            )[0]
            att_id = latest.get("id", "")
            att_name = latest.get("name", "spec.md")
            return _download_planka_attachment_via_minio(att_id, att_name, self._db_url)
        except Exception as e:
            logger.warning("download_latest_spec_attachment failed for card '%s': %s", card_id, e)
            return None

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
            logger.debug("Uploaded spec attachment '%s' to card '%s'.", filename, card_id)
        except Exception as e:
            logger.warning("upload_spec_attachment failed for card '%s': %s", card_id, e)

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
    from framework.db.queries import get_project
    row = get_project(project_id, db_url)
    return (row.get("config") or {}).get("planka_card_id") if row else None


def _set_planka_card_id_in_db(project_id: str, card_id: str, db_url: str | None) -> None:
    from framework.db.connection import get_connection
    with get_connection(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE projects SET config = config || %s::jsonb WHERE id = %s",
                (json.dumps({"planka_card_id": card_id}), project_id),
            )


def _extract_thread_id(description: str) -> str | None:
    if not description:
        return None
    match = re.search(r"thread_id:\s*(\S+)", description)
    return match.group(1) if match else None


def _download_planka_attachment_via_minio(
    att_id: str,
    att_name: str,
    db_url: str | None,
) -> str | None:
    """
    Download a Planka attachment via MinIO.

    Planka stores files in the 'planka-attachments' bucket under
    'private/attachments/{uploadedFileId}/{filename}'.
    The uploadedFileId is read from the shared Planka DB (attachment.data->>'uploadedFileId').
    """
    try:
        from framework.db.connection import get_connection
        with get_connection(db_url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT data->>'uploadedFileId' FROM attachment WHERE id = %s",
                    (att_id,),
                )
                row = cur.fetchone()
        if not row or not row[0]:
            logger.warning("No uploadedFileId for attachment '%s'", att_id)
            return None
        uploaded_file_id = row[0]
    except Exception as e:
        logger.warning("Could not read uploadedFileId for attachment '%s': %s", att_id, e)
        return None

    try:
        import io, os
        from minio import Minio
        endpoint = os.getenv("MINIO_ENDPOINT", "minio:9000").replace("http://", "").replace("https://", "")
        client = Minio(
            endpoint,
            access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
            secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
            secure=False,
        )
        key = f"private/attachments/{uploaded_file_id}/{att_name}"
        bucket = "planka-attachments"
        response = client.get_object(bucket, key)
        try:
            content = response.read().decode("utf-8", errors="replace")
        finally:
            response.close()
            response.release_conn()
        return content
    except Exception as e:
        logger.warning("MinIO download failed for attachment '%s': %s", att_id, e)
        return None
