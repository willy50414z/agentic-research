"""
tests/test_revise_pipeline.py

§8 — End-to-end integration tests for the v2 revise pipeline driven through
``app/workflow/executing_step.py:_run_revise``.

These differ from ``tests/test_revise_v2.py`` (orchestrator unit tests) by
exercising the dispatcher wrapper: state merge, workflow_step transitions,
and Planka artifact uploads. They use the same ScriptedLLM mock pattern as
the v2 tests but additionally assert that:

  - APPROVED revise → workflow_step transitions to IMPLEMENT
  - TERMINATE revise → workflow_step transitions to TERMINATE
  - revised_direction / audit / strategy_spec markdown artifacts are uploaded
    via ``sink.upload_spec_attachment``
  - bundled-backtest types (is_zip / oos_zip etc.) never appear in upload calls
    (covered by ``_upload_new_artifacts`` filter)

§8 task coverage:
  - 8.1: full intent → checklist → subagent → audit → APPROVED happy path
  - 8.2: SUBAGENT_DISHONEST streak → TERMINATE
  - 8.3: deterministic FAIL → subagent retry → APPROVED
  - 8.4: LLM unavailable → TERMINATE (no rule-based fallback)
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

# Import the ScriptedLLM helper + fixtures from the v2 unit tests.
from tests.test_revise_v2 import (  # type: ignore[import-not-found]
    ScriptedLLM,
    _BASE_PY,
    _DISHONEST_COMPLETION_YAML,
    _HONEST_COMPLETION_YAML,
    _NEW_PY_STOPLOSS_CHANGED,
    _VALID_CHECKLIST_YAML,
    _write_checklist,
    _write_intent,
    _write_intent_audit,
    _write_llm3,
    _write_subagent,
)


# ---------------------------------------------------------------------------
# Fakes for sink + DB interactions
# ---------------------------------------------------------------------------


class FakeSink:
    """Minimal WorkflowSink implementation that records calls."""

    def __init__(self, card_id: str | None = "card-1"):
        self._card_id = card_id
        self.uploads: list[tuple[str, str]] = []   # (filename, content_first_chars)
        self.comments: list[str] = []
        self.byte_uploads: list[tuple[str, int, str]] = []  # (filename, size, content_type)

    def resolve_card_id(self, project_id: str) -> str | None:
        return self._card_id

    def post_comment(self, project_id: str, text: str) -> None:
        self.comments.append(text)

    def upload_spec_attachment(self, card_id: str, filename: str, content: str) -> None:
        self.uploads.append((filename, content[:120]))

    def upload_bytes_attachment(self, card_id: str, filename: str, data: bytes, content_type: str) -> None:
        self.byte_uploads.append((filename, len(data), content_type))


# ---------------------------------------------------------------------------
# Common pipeline setup
# ---------------------------------------------------------------------------


@pytest.fixture
def pipeline_env(tmp_path, monkeypatch):
    """Wire env + filesystem so _run_revise drives v2 against tmp_path artifacts.

    Returns a dict with: state, sink, workflow_step_calls, merged_states, artifacts_dir.
    """
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir()
    strat_dir = artifacts_dir / "strategies" / "v0"
    strat_dir.mkdir(parents=True)
    base_py = strat_dir / "FooStrategy.py"
    base_py.write_text(_BASE_PY, encoding="utf-8")

    # Force v2 path
    monkeypatch.setenv("REVISE_PIPELINE_VERSION", "v2")

    # Redirect ARTIFACTS_DIR everywhere it's read
    import app.freqtrade.steps.revise.v2 as v2_mod
    monkeypatch.setattr(v2_mod, "ARTIFACTS_DIR", artifacts_dir)
    import app.freqtrade.steps._common as common_mod
    monkeypatch.setattr(common_mod, "ARTIFACTS_DIR", artifacts_dir)
    import app.workflow.executing_step as exec_mod
    # executing_step imports ARTIFACTS_DIR lazily inside helpers; nothing to patch there

    # Capture DB calls
    workflow_step_calls: list[str] = []
    merged_states: list[dict] = []

    def _fake_set_workflow_step(project_id, step, db_url=None):
        workflow_step_calls.append(str(step.value if hasattr(step, "value") else step))

    def _fake_merge_config(project_id, patch, db_url=None):
        merged_states.append(dict(patch))

    monkeypatch.setattr(exec_mod, "set_workflow_step", _fake_set_workflow_step)
    monkeypatch.setattr(exec_mod, "merge_config", _fake_merge_config)

    sink = FakeSink()

    state = {
        "analyze_attempt": 1,
        "implementation_plan": {
            "strategy_name": "FooStrategy",
            "strategy_file": str(base_py),
            "stoploss": -0.05,
        },
        "last_reason": "OOS dd 0.31",
        "artifacts": [],
    }

    return {
        "state": state,
        "sink": sink,
        "workflow_step_calls": workflow_step_calls,
        "merged_states": merged_states,
        "artifacts_dir": artifacts_dir,
    }


def _drive_run_revise(pipeline_env, llm, project_id="proj-1"):
    """Run `_run_revise` with a ScriptedLLM injected into v2.

    Patches the v2 module's `_default_call_llm` global so the orchestrator
    uses `llm` instead of the real LLM subprocess wrapper.
    """
    import app.freqtrade.steps.revise.v2 as v2_mod
    import app.workflow.executing_step as exec_mod

    with patch.object(v2_mod, "_default_call_llm", new=llm):
        exec_mod._run_revise(
            project_id=project_id,
            state=pipeline_env["state"],
            db_url=None,
            sink=pipeline_env["sink"],
            move_card_fn=None,
        )


# ---------------------------------------------------------------------------
# 8.1 Happy path — full pipeline → APPROVED → uploads + IMPLEMENT
# ---------------------------------------------------------------------------


class TestHappyPathThroughDispatcher:

    def test_approved_triggers_implement_and_uploads_three_artifacts(self, pipeline_env):
        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=True)),
            ("checklist",    lambda cwd, **kw: _write_checklist(cwd)),
            ("subagent",     lambda **kw: _write_subagent(**kw)),
            ("llm3",         lambda cwd, **kw: _write_llm3(cwd, items=[])),
        ])
        _drive_run_revise(pipeline_env, llm)

        # Workflow advances to IMPLEMENT
        assert pipeline_env["workflow_step_calls"] == ["implement"]

        # State merge happened with the new strategy_file path
        merged = pipeline_env["merged_states"]
        assert any(
            (m.get("implementation_plan") or {}).get("strategy_file", "").endswith(".py")
            for m in merged
        )

        # All three markdown artifacts uploaded to Planka
        uploaded_filenames = [u[0] for u in pipeline_env["sink"].uploads]
        assert any(f.startswith("v1_revised_direction") or "revised_direction" in f for f in uploaded_filenames)
        assert any("audit" in f for f in uploaded_filenames)
        assert any("strategy_spec" in f for f in uploaded_filenames)

        # No bundled-backtest types leaked through
        for filename, _ in pipeline_env["sink"].uploads:
            assert not filename.endswith("_is.zip")
            assert not filename.endswith("_oos.zip")
            assert not filename.endswith("_trades.json")
            assert not filename.endswith("_signals.json")
            assert not filename.endswith("_report.html")


# ---------------------------------------------------------------------------
# 8.2 Dishonest streak → TERMINATE
# ---------------------------------------------------------------------------


class TestDishonestThroughDispatcher:

    def test_two_consecutive_dishonest_terminates_workflow(self, pipeline_env):
        # Both subagent attempts write _BASE_PY (no real change) but report completed:true → lying.
        # Each round audit deterministic FAIL → dishonest. Two consecutive → TERMINATE.
        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=True)),
            ("checklist",    lambda cwd, **kw: _write_checklist(cwd)),
            ("subagent", lambda **kw: _write_subagent(
                new_py=_BASE_PY, completion_yaml=_DISHONEST_COMPLETION_YAML, **kw,
            )),
            ("subagent", lambda **kw: _write_subagent(
                new_py=_BASE_PY, completion_yaml=_DISHONEST_COMPLETION_YAML, **kw,
            )),
        ])
        _drive_run_revise(pipeline_env, llm)

        assert pipeline_env["workflow_step_calls"] == ["terminate"]

        # last_result merged as TERMINATE; last_reason mentions SUBAGENT_DISHONEST
        last_merge = pipeline_env["merged_states"][-1] if pipeline_env["merged_states"] else {}
        assert last_merge.get("last_result") == "TERMINATE"
        assert "SUBAGENT_DISHONEST" in last_merge.get("last_reason", "")

        # Even on TERMINATE, audit log was uploaded (revised_direction + audit; no strategy_spec
        # because no promote happened).
        uploaded_filenames = [u[0] for u in pipeline_env["sink"].uploads]
        assert any("audit" in f for f in uploaded_filenames)
        assert not any("strategy_spec" in f for f in uploaded_filenames)


# ---------------------------------------------------------------------------
# 8.3 Deterministic FAIL → subagent retry → APPROVED
# ---------------------------------------------------------------------------


class TestRetryThenApproved:

    def test_implementation_failed_then_pass(self, pipeline_env):
        # Attempt 0: subagent self-reports completed:false (incomplete; not lying)
        #   → IMPLEMENTATION_FAILED route → subagent_retry +=1, no audit run
        # Attempt 1: subagent writes correct change + honest report → audit PASS → promote
        from tests.test_revise_v2 import _INCOMPLETE_COMPLETION_YAML

        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=True)),
            ("checklist",    lambda cwd, **kw: _write_checklist(cwd)),
            ("subagent", lambda **kw: _write_subagent(
                new_py=_BASE_PY, completion_yaml=_INCOMPLETE_COMPLETION_YAML, **kw,
            )),
            ("subagent",     lambda **kw: _write_subagent(**kw)),
            ("llm3",         lambda cwd, **kw: _write_llm3(cwd, items=[])),
        ])
        _drive_run_revise(pipeline_env, llm)

        assert pipeline_env["workflow_step_calls"] == ["implement"]
        # strategy_file points to v1/ promoted location
        last_merge = pipeline_env["merged_states"][-1]
        promoted = last_merge.get("implementation_plan", {}).get("strategy_file", "")
        assert "strategies" in promoted and "v1" in promoted
        assert ".staging" not in promoted


# ---------------------------------------------------------------------------
# 8.4 LLM unavailable → TERMINATE (no rule-based fallback)
# ---------------------------------------------------------------------------


class TestLLMUnavailableThroughDispatcher:

    def test_llm_subprocess_failure_terminates_workflow_no_fallback(self, pipeline_env):
        def _failing(prompt, cwd=None):
            raise RuntimeError("freqtrade LLM subprocess crashed")

        _drive_run_revise(pipeline_env, _failing)

        assert pipeline_env["workflow_step_calls"] == ["terminate"]
        last_merge = pipeline_env["merged_states"][-1] if pipeline_env["merged_states"] else {}
        assert last_merge.get("last_result") == "TERMINATE"

        # Confirm the v1 rule-based fallback is NOT silently triggered:
        # plan.strategy_file should remain pointing at the baseline v0 file
        plan = last_merge.get("implementation_plan")
        if plan:
            # If plan was returned at all, strategy_file must NOT have been
            # rewritten to a v1/ path (which would only happen on APPROVED promote).
            assert "v1" not in plan.get("strategy_file", "")

        # No strategy_spec should be uploaded (no promote happened)
        uploaded_filenames = [u[0] for u in pipeline_env["sink"].uploads]
        assert not any("strategy_spec" in f for f in uploaded_filenames)
