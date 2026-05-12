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
    config.addinivalue_line(
        "markers",
        "freqtrade_real: requires a real Freqtrade CLI installation; skipped by default",
    )


def _mock_optional_deps():
    """
    Stub out optional dependencies so unit tests can import framework modules
    without a full environment installed.  Integration tests that need real
    DB connectivity are skipped when DATABASE_URL is unset.
    """
    for mod in ("psycopg", "psycopg.rows", "psycopg_pool"):
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()

    # httpx is used by server.py — stub if not installed
    if "httpx" not in sys.modules:
        sys.modules["httpx"] = MagicMock()

    # fastapi / uvicorn — stub if not installed
    for mod in ("fastapi", "fastapi.responses", "uvicorn"):
        if mod not in sys.modules:
            sys.modules[mod] = MagicMock()


_mock_optional_deps()
