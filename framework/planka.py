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
        Uses POST /api/comment-actions {"cardId": ..., "text": ...}.
        Non-blocking: logs warning on failure.
        """
        card_id = self.resolve_card_id(project_id)
        if not card_id:
            logger.debug("post_comment: no card found for project '%s', skipping.", project_id)
            return
        try:
            resp = httpx.post(
                f"{self._url}/api/comment-actions",
                headers={"Authorization": f"Bearer {self._token}"},
                json={"cardId": card_id, "text": text},
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
            if _extract_thread_id(card.get("description", "")) == project_id:
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
    match = re.search(r"thread_id:\s*(\S+)", description)
    return match.group(1) if match else None
