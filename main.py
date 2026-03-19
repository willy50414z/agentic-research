"""
main.py — FastAPI application entry point.

Registers all plugins, then mounts the framework API router.
Uvicorn launches this file (see Dockerfile CMD).
"""

import logging
from dotenv import load_dotenv

load_dotenv()

# Register plugins (import triggers @register decorator)
import projects.dummy.plugin       # noqa: F401
import projects.demo.plugin        # noqa: F401
import projects.quant_alpha.plugin # noqa: F401

from framework.api.server import app  # noqa: F401 — re-exported for uvicorn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
