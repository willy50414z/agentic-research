"""
app/workflow/error_report.py

Writes artifacts/errors/last_error.txt when a workflow step fails.
Called from dispatch_step and spec_review_step except blocks.
"""

import logging
import os
import traceback
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_ERRORS_DIR = Path("artifacts") / "errors"
_ERROR_FILE = _ERRORS_DIR / "last_error.txt"

_ENV_KEYS = [
    "BACKTEST_MODE",
    "LLM_FREQTRADE_STEPS",
    "LLM_DEFAULT",
    "LLM_REVISE_LLM1",
    "LLM_REVISE_LLM2",
    "LLM_REVISE_AUDIT",
    "PLANKA_API_URL",
    "DATABASE_URL",
]


def write_error_report(
    project_id: str,
    exc: BaseException,
    step: str,
    db_url: str | None = None,
) -> None:
    """Write a structured error report to artifacts/errors/last_error.txt.

    Never raises — all failures are logged as warnings.
    """
    try:
        _write(project_id, exc, step, db_url)
    except Exception as e:
        logger.warning("[error_report] failed to write last_error.txt: %s", e)


def _write(project_id: str, exc: BaseException, step: str, db_url: str | None) -> None:
    card_id = "(unavailable)"
    planka_url = "(unavailable)"
    workflow_step = "(unavailable)"
    last_result = "(unavailable)"
    last_reason = "(unavailable)"
    review_in_progress = "(unavailable)"
    spec_keys = "(unavailable)"

    try:
        from app.db.queries import get_project  # local import avoids circular deps
        project = get_project(project_id, db_url)
        if project:
            cfg = project.get("config") or {}
            raw_card_id = cfg.get("planka_card_id")
            if raw_card_id:
                card_id = str(raw_card_id)
                planka_base = os.environ.get("PLANKA_API_URL", "").rstrip("/")
                if planka_base:
                    planka_url = f"{planka_base}/cards/{card_id}"
            workflow_step = project.get("workflow_step") or "(not set)"
            last_result = cfg.get("last_result", "(not set)")
            last_reason = str(cfg.get("last_reason", "(not set)"))
            review_in_progress = str(cfg.get("review_in_progress", False))
            spec = cfg.get("spec") or {}
            spec_keys = ", ".join(sorted(spec.keys())) if spec else "(empty)"
    except Exception as db_exc:
        logger.warning("[error_report] DB query failed: %s", db_exc)

    env_lines = "\n".join(
        f"  {k}={os.environ.get(k, '(not set)')}" for k in _ENV_KEYS
    )

    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))

    report = (
        f"=== ERROR REPORT ===\n"
        f"timestamp:          {datetime.utcnow().isoformat()}Z\n"
        f"project_id:         {project_id}\n"
        f"step:               {step}\n"
        f"card_id:            {card_id}\n"
        f"planka_url:         {planka_url}\n"
        f"\n"
        f"=== STATUS ===\n"
        f"workflow_step:      {workflow_step}\n"
        f"last_result:        {last_result}\n"
        f"last_reason:        {last_reason}\n"
        f"review_in_progress: {review_in_progress}\n"
        f"\n"
        f"=== SPEC (top-level keys) ===\n"
        f"{spec_keys}\n"
        f"\n"
        f"=== ENVIRONMENT ===\n"
        f"{env_lines}\n"
        f"\n"
        f"=== TRACEBACK ===\n"
        f"{tb}"
    )

    _ERRORS_DIR.mkdir(parents=True, exist_ok=True)
    _ERROR_FILE.write_text(report, encoding="utf-8")
    logger.info("[error_report] wrote %s", _ERROR_FILE)
