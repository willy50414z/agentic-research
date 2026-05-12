"""
main.py — FastAPI application entry point.

Uvicorn launches this file (see Dockerfile CMD).
"""

import logging

import llm_eval
from dotenv import load_dotenv
from llm_eval import LLMTarget, Outcome
from pip._internal.utils import subprocess
from llm_eval.preflight import check_target
load_dotenv()

# Configure logging BEFORE importing any framework module so all loggers inherit the handler.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    force=True,  # override any handlers already installed by uvicorn/third-party at import time
)

from app.api.server import app  # noqa: F401 — re-exported for uvicorn

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=["app"],
    )