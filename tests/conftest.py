"""
tests/conftest.py — pytest configuration and shared fixtures.
"""

import sys
from unittest.mock import MagicMock


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: mark test as requiring a live PostgreSQL DATABASE_URL",
    )


def _mock_psycopg():
    """
    Stub out psycopg (and langgraph.checkpoint.postgres) so unit tests
    can import framework.graph without a real database driver installed.
    Integration tests that actually invoke the graph will still need the
    real driver — they are skipped when DATABASE_URL is unset.
    """
    for mod in ("psycopg", "psycopg.rows", "psycopg_pool"):
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

    if "langgraph.checkpoint.postgres" not in sys.modules:
        pg_saver_mock = MagicMock()
        pg_saver_mock.PostgresSaver.return_value = MagicMock()
        sys.modules["langgraph.checkpoint.postgres"] = pg_saver_mock

    # httpx is used by server.py — stub if not installed
    if "httpx" not in sys.modules:
        sys.modules["httpx"] = MagicMock()

    # fastapi / uvicorn — stub if not installed
    for mod in ("fastapi", "fastapi.responses", "uvicorn"):
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()


# Apply stubs early so framework.graph can be imported in unit tests
_mock_psycopg()
