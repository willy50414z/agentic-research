"""revise_step orchestrator.

Dispatches to v1 or v2 based on the ``REVISE_PIPELINE_VERSION`` env var:
  - ``v1`` (default): legacy 2-LLM JSON validation flow
  - ``v2``: intent → checklist → subagent → audit (added in §3 of the
    revise-pipeline-checklist-audit change)

Per spec ``fail-path-revise-plan`` and design D12, the version is read once
per dispatch entry, then persisted to ``projects.config.revise_pipeline_version``
so a project's revise rounds remain consistent if the env flag changes mid-run.
This package exposes ``revise_step``; the per-project persistence is handled
by the caller (``app/workflow/executing_step.py``) — this module simply honours
``state["revise_pipeline_version"]`` when present, falling back to the env var.
"""
from __future__ import annotations

import logging
import os

from .v1 import revise_step_v1

logger = logging.getLogger(__name__)


def _resolve_version(state: dict) -> str:
    """Resolve which revise pipeline to run for this state.

    Precedence:
      1. ``state["revise_pipeline_version"]`` (project-locked value)
      2. ``REVISE_PIPELINE_VERSION`` env var
      3. ``"v1"`` default
    Invalid values fall back to ``"v1"`` with a warning.
    """
    locked = state.get("revise_pipeline_version")
    if locked:
        if locked not in ("v1", "v2"):
            logger.warning(
                "revise: project-locked revise_pipeline_version=%r is invalid; falling back to v1",
                locked,
            )
            return "v1"
        return locked

    raw = os.getenv("REVISE_PIPELINE_VERSION", "v1") or "v1"
    if raw not in ("v1", "v2"):
        logger.warning(
            "revise: REVISE_PIPELINE_VERSION=%r is invalid; falling back to v1",
            raw,
        )
        return "v1"
    return raw


def revise_step(state: dict) -> dict:
    """Run the configured revise pipeline.

    Currently only v1 is implemented. v2 is added by §3 of the
    revise-pipeline-checklist-audit change.
    """
    version = _resolve_version(state)
    if version == "v2":
        # v2 implementation lives in revise/v2.py (added in §3)
        try:
            from .v2 import revise_step_v2
        except ImportError:
            logger.warning(
                "revise: REVISE_PIPELINE_VERSION=v2 requested but v2 module not yet "
                "implemented; falling back to v1",
            )
            return revise_step_v1(state)
        return revise_step_v2(state)
    return revise_step_v1(state)
