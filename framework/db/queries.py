"""
framework/db/queries.py

CRUD helpers for the business schema tables:
  - projects
  - loop_metrics
  - checkpoint_decisions
"""

import json
import logging
from datetime import datetime, timezone

from .connection import get_connection

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# projects
# ---------------------------------------------------------------------------

def create_project(
    project_id: str,
    name: str,
    plugin_name: str,
    goal: str,
    config: dict | None = None,
    db_url: str | None = None,
) -> None:
    with get_connection(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO projects (id, name, plugin_name, goal, config)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    name        = EXCLUDED.name,
                    plugin_name = EXCLUDED.plugin_name,
                    goal        = EXCLUDED.goal,
                    config      = EXCLUDED.config
                """,
                (project_id, name, plugin_name, goal, json.dumps(config or {})),
            )
    logger.info("Project '%s' upserted.", project_id)


def get_project(project_id: str, db_url: str | None = None) -> dict | None:
    with get_connection(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, plugin_name, goal, config, created_at FROM projects WHERE id = %s",
                (project_id,),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "name": row[1], "plugin_name": row[2],
        "goal": row[3], "config": row[4], "created_at": row[5],
    }


def get_planka_card_id(project_id: str, db_url: str | None = None) -> str | None:
    """Read planka_card_id from projects.config JSONB."""
    row = get_project(project_id, db_url)
    return (row.get("config") or {}).get("planka_card_id") if row else None


def set_planka_card_id(project_id: str, card_id: str, db_url: str | None = None) -> None:
    """Merge planka_card_id into projects.config JSONB."""
    with get_connection(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE projects SET config = config || %s::jsonb WHERE id = %s",
                (json.dumps({"planka_card_id": card_id}), project_id),
            )
    logger.debug("planka_card_id persisted for project '%s'.", project_id)


# ---------------------------------------------------------------------------
# loop_metrics
# ---------------------------------------------------------------------------

def record_loop_metrics(
    project_id: str,
    loop_index: int,
    result: str,
    reason: str | None = None,
    report_path: str | None = None,
    metrics: dict | None = None,
    db_url: str | None = None,
) -> None:
    """
    Write one row to loop_metrics after each PASS/FAIL.

    metrics: optional dict with domain-specific keys like win_rate, alpha_ratio, etc.
    """
    m = metrics or {}
    with get_connection(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO loop_metrics
                    (project_id, loop_index, win_rate, alpha_ratio, max_drawdown,
                     is_profit_factor, oos_profit_factor, result, reason, report_minio_key)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (project_id, loop_index) DO UPDATE SET
                    result = EXCLUDED.result,
                    reason = EXCLUDED.reason,
                    report_minio_key = EXCLUDED.report_minio_key,
                    recorded_at = NOW()
                """,
                (
                    project_id, loop_index,
                    m.get("win_rate"), m.get("alpha_ratio"), m.get("max_drawdown"),
                    m.get("is_profit_factor"), m.get("oos_profit_factor"),
                    result, reason, report_path,
                ),
            )
    logger.info("loop_metrics recorded: project=%s loop=%d result=%s", project_id, loop_index, result)


def get_loop_metrics(project_id: str, db_url: str | None = None) -> list[dict]:
    with get_connection(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT loop_index, win_rate, alpha_ratio, max_drawdown,
                       is_profit_factor, oos_profit_factor, result, reason, report_minio_key, recorded_at
                FROM loop_metrics
                WHERE project_id = %s
                ORDER BY loop_index
                """,
                (project_id,),
            )
            rows = cur.fetchall()
    return [
        {
            "loop_index": r[0], "win_rate": r[1], "alpha_ratio": r[2],
            "max_drawdown": r[3], "is_profit_factor": r[4], "oos_profit_factor": r[5],
            "result": r[6], "reason": r[7], "report_path": r[8], "recorded_at": r[9],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# checkpoint_decisions
# ---------------------------------------------------------------------------

def record_checkpoint_decision(
    project_id: str,
    loop_index: int,
    action: str,
    notes: str | None = None,
    modified_plan: dict | None = None,
    db_url: str | None = None,
) -> None:
    with get_connection(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO checkpoint_decisions (project_id, loop_index, action, notes, modified_plan)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (project_id, loop_index, action, notes, json.dumps(modified_plan) if modified_plan else None),
            )
    logger.info(
        "checkpoint_decision recorded: project=%s loop=%d action=%s",
        project_id, loop_index, action,
    )
