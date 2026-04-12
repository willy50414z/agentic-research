"""
main.py — FastAPI application entry point.

Registers all plugins, then mounts the framework API router.
Uvicorn launches this file (see Dockerfile CMD).
"""

import logging
from dotenv import load_dotenv

load_dotenv()

# Configure logging BEFORE importing any framework module so all loggers inherit the handler.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True,  # override any handlers already installed by uvicorn/third-party at import time
)

# Auto-discover and register all plugins under projects/*/plugin.py
from framework.plugin_registry import discover_plugins as _discover_plugins
_discover_plugins()

from framework.api.server import app  # noqa: F401 — re-exported for uvicorn

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
