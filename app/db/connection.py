"""
framework/db/connection.py

psycopg3 connection pool for the business schema tables.
Uses psycopg_pool.ConnectionPool (min=1, max=5) — safe for multi-process deployments.
(LangGraph's checkpointer manages its own dedicated connection separately.)
"""

import os
import logging
from contextlib import contextmanager

import psycopg
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

_pools: dict[str, ConnectionPool] = {}


def _get_pool(db_url: str) -> ConnectionPool:
    """Return (and lazily create) a connection pool for the given URL."""
    pool = _pools.get(db_url)
    if pool is None:
        logger.info("Initialising connection pool for %s (min=1, max=5).", _safe_dsn(db_url))
        pool = ConnectionPool(
            conninfo=db_url,
            min_size=1,
            max_size=5,
            open=True,
            kwargs={"autocommit": True},
        )
        _pools[db_url] = pool
    return pool


def _safe_dsn(db_url: str) -> str:
    """Strip credentials from a Postgres URL for logging."""
    import re
    return re.sub(r"://[^@]+@", "://***@", db_url)


@contextmanager
def get_connection(db_url: str | None = None):
    """
    Context manager that borrows a connection from the pool and returns it on exit.

    Usage:
        with get_connection(db_url) as conn:
            with conn.cursor() as cur:
                cur.Executing(...)
    """
    url = db_url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set.")
    with _get_pool(url).connection() as conn:
        yield conn


def run_migration(sql_path: str, db_url: str | None = None) -> None:
    """Execute a SQL migration file against the database."""
    with get_connection(db_url) as conn:
        with open(sql_path, "r", encoding="utf-8") as f:
            sql = f.read()
        with conn.cursor() as cur:
            cur.execute(sql)
    logger.info("Migration applied: %s", sql_path)
