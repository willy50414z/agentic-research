"""
framework/db/connection.py

psycopg3 connection helper for the business schema tables.
(LangGraph's checkpointer manages its own connection separately.)
"""

import os
import logging
import psycopg

logger = logging.getLogger(__name__)

_conn: psycopg.Connection | None = None


def get_connection(db_url: str | None = None) -> psycopg.Connection:
    """
    Return (and lazily create) a module-level psycopg3 connection.
    Thread-safe for single-process use; for multi-process use a pool.
    """
    # TODO(phase4): migrate to AsyncConnectionPool (psycopg3) for multi-process safety
    global _conn
    url = db_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")

    if _conn is None or _conn.closed:
        logger.info("Opening database connection to business schema.")
        _conn = psycopg.connect(url, autocommit=True)

    return _conn


def run_migration(sql_path: str, db_url: str | None = None) -> None:
    """Execute a SQL migration file against the database."""
    conn = get_connection(db_url)
    with open(sql_path, "r") as f:
        sql = f.read()
    with conn.cursor() as cur:
        cur.execute(sql)
    logger.info("Migration applied: %s", sql_path)
