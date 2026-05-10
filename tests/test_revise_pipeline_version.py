"""
tests/test_revise_pipeline_version.py

§10.4 — Tests for the REVISE_PIPELINE_VERSION feature flag locking semantics
defined in spec ``fail-path-revise-plan`` (Requirement: REVISE_PIPELINE_VERSION
feature flag) and design D12.

Three scenarios are covered:

  1. env unset + project unlocked
       → ``_run_revise`` resolves to ``"v1"``, persists it via
         ``merge_config`` and dispatches the v1 path.
  2. env=v2 + project unlocked
       → ``_run_revise`` resolves to ``"v2"``, persists it and dispatches v2.
  3. project already locked to v2 + env changed to v1 mid-stream
       → no new ``revise_pipeline_version`` write; v2 path still runs.
         (Lock wins regardless of env var; spec invariant: version cannot
          change during a project's lifetime.)

These tests do not exercise the real LLM/v2 orchestrator. ``revise_step_v1``
and ``revise_step_v2`` are patched to identity-like stubs so we can observe
*which* path was selected without running the full pipeline.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fakes mirroring tests/test_revise_pipeline.py (kept local to avoid coupling)
# ---------------------------------------------------------------------------


class FakeSink:
    def __init__(self, card_id: str | None = "card-1"):
        self._card_id = card_id
        self.uploads: list[tuple[str, str]] = []
        self.byte_uploads: list[tuple[str, int, str]] = []

    def resolve_card_id(self, project_id: str) -> str | None:
        return self._card_id

    def post_comment(self, project_id: str, text: str) -> None:
        pass

    def upload_spec_attachment(self, card_id: str, filename: str, content: str) -> None:
        self.uploads.append((filename, content[:120]))

    def upload_bytes_attachment(self, card_id: str, filename: str, data: bytes, content_type: str) -> None:
        self.byte_uploads.append((filename, len(data), content_type))


@pytest.fixture
def captured(monkeypatch):
    """Patch DB writers + the v1/v2 entry points and capture invocations."""
    import app.workflow.executing_step as exec_mod
    import app.freqtrade.steps.revise as revise_pkg
    import app.freqtrade.steps.revise.v1 as v1_mod

    workflow_step_calls: list[str] = []
    merge_calls: list[dict] = []

    def _fake_set_workflow_step(project_id, step, db_url=None):
        workflow_step_calls.append(str(step.value if hasattr(step, "value") else step))

    def _fake_merge_config(project_id, patch_dict, db_url=None):
        merge_calls.append(dict(patch_dict))

    monkeypatch.setattr(exec_mod, "set_workflow_step", _fake_set_workflow_step)
    monkeypatch.setattr(exec_mod, "merge_config", _fake_merge_config)

    # Stub return shape: just enough for _run_revise's APPROVED branch.
    _STUB_RESULT = {
        "last_result": "REVISED",
        "implementation_plan": {"strategy_file": "/tmp/stub.py"},
        "artifacts": [],
    }

    v1_calls: list[dict] = []
    v2_calls: list[dict] = []

    def _v1_stub(state):
        v1_calls.append(dict(state))
        return dict(_STUB_RESULT)

    def _v2_stub(state):
        v2_calls.append(dict(state))
        return dict(_STUB_RESULT)

    # Patch the v1 path at its definition site. The dispatcher in
    # revise/__init__.py imports ``revise_step_v1`` at module import time, so
    # patch the module attribute the dispatcher actually consults.
    monkeypatch.setattr(revise_pkg, "revise_step_v1", _v1_stub)
    monkeypatch.setattr(v1_mod, "revise_step_v1", _v1_stub)

    # The v2 path is lazy-imported inside ``revise_step``. Inject a fake module
    # so ``from .v2 import revise_step_v2`` returns our stub without loading
    # the real orchestrator (which would require LLM + filesystem fixtures).
    import sys
    import types

    fake_v2 = types.ModuleType("app.freqtrade.steps.revise.v2")
    fake_v2.revise_step_v2 = _v2_stub
    monkeypatch.setitem(sys.modules, "app.freqtrade.steps.revise.v2", fake_v2)

    return {
        "workflow_step_calls": workflow_step_calls,
        "merge_calls": merge_calls,
        "v1_calls": v1_calls,
        "v2_calls": v2_calls,
    }


def _drive(state, project_id="proj-x"):
    import app.workflow.executing_step as exec_mod

    sink = FakeSink()
    exec_mod._run_revise(
        project_id=project_id,
        state=state,
        db_url=None,
        sink=sink,
        move_card_fn=None,
    )
    return sink


# ---------------------------------------------------------------------------
# Scenario 1 — env unset + project unlocked → lock v1, run v1
# ---------------------------------------------------------------------------


def test_env_unset_project_unlocked_locks_v1_and_runs_v1(captured, monkeypatch):
    monkeypatch.delenv("REVISE_PIPELINE_VERSION", raising=False)

    state = {"artifacts": [], "revise_pipeline_version": None}
    _drive(state)

    # Lock written via merge_config with v1.
    version_writes = [
        m for m in captured["merge_calls"] if "revise_pipeline_version" in m
    ]
    assert len(version_writes) == 1
    assert version_writes[0]["revise_pipeline_version"] == "v1"

    # In-flight state was patched so revise_step's _resolve_version sees v1.
    assert state["revise_pipeline_version"] == "v1"

    # v1 path ran, v2 did not.
    assert len(captured["v1_calls"]) == 1
    assert captured["v2_calls"] == []
    assert captured["v1_calls"][0]["revise_pipeline_version"] == "v1"


# ---------------------------------------------------------------------------
# Scenario 2 — env=v2 + project unlocked → lock v2, run v2
# ---------------------------------------------------------------------------


def test_env_v2_project_unlocked_locks_v2_and_runs_v2(captured, monkeypatch):
    monkeypatch.setenv("REVISE_PIPELINE_VERSION", "v2")

    state = {"artifacts": [], "revise_pipeline_version": None}
    _drive(state)

    version_writes = [
        m for m in captured["merge_calls"] if "revise_pipeline_version" in m
    ]
    assert len(version_writes) == 1
    assert version_writes[0]["revise_pipeline_version"] == "v2"
    assert state["revise_pipeline_version"] == "v2"

    assert len(captured["v2_calls"]) == 1
    assert captured["v1_calls"] == []
    assert captured["v2_calls"][0]["revise_pipeline_version"] == "v2"


# ---------------------------------------------------------------------------
# Scenario 3 — project already locked to v2, env=v1 → lock wins, no rewrite
# ---------------------------------------------------------------------------


def test_project_locked_v2_env_v1_lock_wins(captured, monkeypatch):
    monkeypatch.setenv("REVISE_PIPELINE_VERSION", "v1")

    state = {
        "artifacts": [],
        "revise_pipeline_version": "v2",  # already locked from a prior dispatch
    }
    _drive(state)

    # No new write of revise_pipeline_version (other merges from updates may
    # exist but must not rewrite the version key).
    version_writes = [
        m for m in captured["merge_calls"] if "revise_pipeline_version" in m
    ]
    assert version_writes == []

    # v2 still ran, v1 did not.
    assert len(captured["v2_calls"]) == 1
    assert captured["v1_calls"] == []
    assert captured["v2_calls"][0]["revise_pipeline_version"] == "v2"
    assert state["revise_pipeline_version"] == "v2"
