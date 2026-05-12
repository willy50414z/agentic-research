"""
tests/test_revise_v2.py

Coverage for ``app/freqtrade/steps/revise/v2.py`` — the §3 orchestrator.

Tests mock ``_call_llm`` with a :class:`ScriptedLLM` that writes the expected
output files for each stage based on a programmable scenario. Each test
exercises one decision branch of the orchestrator:

  - happy path through all 5 stages → APPROVED + promote
  - intent_retry loop (REJECTED then APPROVED)
  - intent_retry exhausted → TERMINATE
  - checklist_retry via UNIMPLEMENTABLE_CHECKLIST
  - checklist_retry via CHECKLIST_AMBIGUOUS (LLM3 INSUFFICIENT)
  - subagent_retry via IMPLEMENTATION_FAILED then PASS
  - subagent_retry resets on new checklist
  - SUBAGENT_DISHONEST streak (two consecutive)
  - LLM unavailable → TERMINATE (no rule-based fallback)
  - promote atomicity (failed promote → TERMINATE preserving staging)

The full subagent prompts are not validated here (covered by ``test_audit.py``
and the prompt-design review). These tests focus on orchestrator state
transitions, retry counter semantics, and artifact emission.
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from app.freqtrade.steps.revise.v2 import revise_step_v2, MAX_RETRIES


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_BASE_PY = textwrap.dedent('''
    """module"""
    from freqtrade.strategy import IStrategy, IntParameter
    import talib.abstract as ta
    from pandas import DataFrame


    class FooStrategy(IStrategy):
        """class docstring"""

        timeframe = "4h"
        stoploss = -0.05
        minimal_roi = {"0": 0.10}

        order_types = {
            "entry": "market",
            "exit": "market",
            "stoploss": "market",
            "stoploss_on_exchange": False,
        }

        rsi_period = IntParameter(10, 20, default=14, space="buy", optimize=True)

        def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
            dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)
            return dataframe

        def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
            dataframe.loc[(dataframe["rsi"] < 30), "enter_long"] = 1
            return dataframe

        def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
            dataframe.loc[(dataframe["rsi"] > 70), "exit_long"] = 1
            return dataframe
''').strip() + "\n"


_NEW_PY_STOPLOSS_CHANGED = _BASE_PY.replace("stoploss = -0.05", "stoploss = -0.03")


_VALID_CHECKLIST_YAML = textwrap.dedent("""
    iteration: 1
    based_on_intent: revision_intent.md
    created_by: llm2
    locked: true
    invariants:
      - timeframe_unchanged
      - class_name_unchanged
      - order_types_four_keys
      - no_lookahead_pattern
    items:
      - id: M1
        type: param
        target:
          path: stoploss
        from: -0.05
        to: -0.03
        rationale: tighten drawdown
""").strip() + "\n"


_HONEST_COMPLETION_YAML = textwrap.dedent("""
    iteration: 1
    attempt: 0
    items:
      - id: M1
        completed: true
        location: candidate.py:13
    unimplementable_items: []
""").strip() + "\n"


_DISHONEST_COMPLETION_YAML = _HONEST_COMPLETION_YAML  # subagent says done; .py won't reflect it


_UNIMPLEMENTABLE_COMPLETION_YAML = textwrap.dedent("""
    iteration: 1
    attempt: 0
    items:
      - id: M1
        completed: false
        blocking_reason: "checklist 要求改 stoploss 但找不到目標"
    unimplementable_items: [M1]
""").strip() + "\n"


_INCOMPLETE_COMPLETION_YAML = textwrap.dedent("""
    iteration: 1
    attempt: 0
    items:
      - id: M1
        completed: false
    unimplementable_items: []
""").strip() + "\n"


# ---------------------------------------------------------------------------
# ScriptedLLM mock
# ---------------------------------------------------------------------------


class ScriptedLLM:
    """Mock ``_call_llm`` that detects the stage from prompt content and runs a writer.

    Usage::

        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: write_intent_md(cwd, "...")),
            ("intent_audit", lambda cwd, **kw: write_intent_audit(cwd, approved=True)),
            ("checklist",    lambda cwd, **kw: write_checklist(cwd, _VALID_CHECKLIST_YAML)),
            ("subagent",     lambda cwd, candidate_path, completion_path, **kw: ...),
            ("llm3",         lambda cwd, **kw: write_llm3(cwd, [{"id":"M1","verdict":"PASS",...}])),
        ])
        revise_step_v2(state, call_llm=llm)
    """

    def __init__(self, script: list[tuple[str, callable]]):
        self.script = list(script)
        self.calls: list[dict] = []

    def __call__(self, prompt: str, cwd: str | None = None) -> str:
        stage = self._detect_stage(prompt)
        self.calls.append({"stage": stage, "cwd": cwd, "prompt_len": len(prompt)})

        if not self.script:
            raise AssertionError(f"ScriptedLLM ran out of script entries (next={stage})")
        expected_stage, writer = self.script.pop(0)
        if expected_stage and expected_stage != stage:
            raise AssertionError(
                f"ScriptedLLM expected stage={expected_stage!r} but prompt looks like {stage!r}"
            )
        # invoke writer with cwd + extracted paths from prompt
        kwargs = {"cwd": Path(cwd)}
        if stage == "subagent":
            kwargs["candidate_path"] = self._extract_path(prompt, "{CANDIDATE_PY_PATH}", default=None)
            kwargs["completion_path"] = self._extract_path(prompt, "{COMPLETION_REPORT_PATH}", default=None)
            # recover concrete paths the orchestrator wrote into the prompt
            kwargs["candidate_path"] = self._scan_for_path(prompt, "candidate.py")
            kwargs["completion_path"] = self._scan_for_path(prompt, "completion_report.yaml")
        writer(**kwargs)
        return ""

    @staticmethod
    def _detect_stage(prompt: str) -> str:
        if "Freqtrade 量化策略修訂 agent" in prompt:
            return "intent"
        if "修訂方向審核 agent" in prompt:
            return "intent_audit"
        if "修訂 checklist 翻譯 agent" in prompt:
            return "checklist"
        if "Freqtrade 策略 subagent" in prompt:
            return "subagent"
        if "strategy audit agent" in prompt:
            return "llm3"
        return "unknown"

    @staticmethod
    def _extract_path(prompt: str, marker: str, default):
        return default

    @staticmethod
    def _scan_for_path(prompt: str, basename: str) -> Path | None:
        # Extract an absolute file path ending in ``basename`` regardless of
        # punctuation around it (the prompt may sandwich the path in CJK
        # punctuation/backticks). Use a regex that grabs everything from a
        # path-like prefix (drive letter or /) up to the basename.
        import re
        pattern = (
            # match Windows (X:\...) or POSIX (/...) absolute path ending in basename
            r"([A-Za-z]:[\\/][^\s`'\"]*?" + re.escape(basename) + r"|/[^\s`'\"]*?" + re.escape(basename) + r")"
        )
        m = re.search(pattern, prompt)
        return Path(m.group(1)) if m else None


# ---------------------------------------------------------------------------
# Stage writer helpers
# ---------------------------------------------------------------------------


def _write_intent(cwd: Path, *, terminate: bool = False, **_) -> None:
    (cwd / "revision_intent.md").write_text("# 修訂方向\n收緊 stoploss\n", encoding="utf-8")
    if terminate:
        (cwd / "revision_intent_status.txt").write_text("TERMINATE\n", encoding="utf-8")


def _write_intent_audit(cwd: Path, *, approved: bool, **_) -> None:
    if approved:
        (cwd / "revision_intent_audit_result.txt").write_text(
            "APPROVED\n方向合理，可進入下一階段。\n", encoding="utf-8",
        )
    else:
        (cwd / "revision_intent_audit_result.txt").write_text(
            "REJECTED\n方向不對症。\n", encoding="utf-8",
        )
        (cwd / "revision_intent_audit_feedback.md").write_text(
            "# 審核意見\n你的方向沒解到 OOS 過擬合。", encoding="utf-8",
        )


def _write_checklist(cwd: Path, yaml_str: str = _VALID_CHECKLIST_YAML, **_) -> None:
    (cwd / "checklist.yaml").write_text(yaml_str, encoding="utf-8")


def _write_subagent(
    *, candidate_path: Path, completion_path: Path,
    new_py: str = _NEW_PY_STOPLOSS_CHANGED,
    completion_yaml: str = _HONEST_COMPLETION_YAML, **_,
) -> None:
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    completion_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_text(new_py, encoding="utf-8")
    completion_path.write_text(completion_yaml, encoding="utf-8")


def _write_llm3(cwd: Path, *, items: list[dict], **_) -> None:
    import yaml as _yaml
    (cwd / "llm3_audit_result.yaml").write_text(
        _yaml.safe_dump({"items": items}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Common state factory
# ---------------------------------------------------------------------------


@pytest.fixture
def state(tmp_path, monkeypatch):
    """Build a state dict + redirect ARTIFACTS_DIR into tmp_path for isolation."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    # write baseline strategy file
    strat_dir = artifacts / "strategies" / "v0"
    strat_dir.mkdir(parents=True)
    base_py = strat_dir / "FooStrategy.py"
    base_py.write_text(_BASE_PY, encoding="utf-8")

    # the orchestrator reads ARTIFACTS_DIR from _common at module import time;
    # patch it explicitly
    import app.freqtrade.steps.revise.v2 as v2_mod
    monkeypatch.setattr(v2_mod, "ARTIFACTS_DIR", artifacts)

    return {
        "analyze_attempt": 1,
        "spec": {
            "trading_scope": {"pair": "BTC/USDT", "timeframe": "4h", "exchange": "binance"},
            "execution": {"fee": "0.10%"},
        },
        "implementation_plan": {
            "strategy_name": "FooStrategy",
            "strategy_file": str(base_py),
            "stoploss": -0.05,
        },
        "last_reason": "OOS drawdown 0.31 > 0.20",
        "artifacts": [],
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestHappyPath:

    def test_full_pipeline_promotes_candidate(self, state, tmp_path):
        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=True)),
            ("checklist",    lambda cwd, **kw: _write_checklist(cwd)),
            ("subagent",     lambda **kw: _write_subagent(**kw)),
            ("llm3",         lambda cwd, **kw: _write_llm3(cwd, items=[])),  # no logic items, vacuous PASS
        ])
        result = revise_step_v2(state, call_llm=llm)
        assert result["last_result"] == "REVISED"
        assert "APPROVED" in result["last_reason"]
        # plan.strategy_file must point to the promoted v1 path
        promoted = Path(result["implementation_plan"]["strategy_file"])
        assert promoted.parent.name == "v1"
        assert promoted.exists()
        # revised_direction + audit artifacts attached
        types = {a["type"] for a in result["artifacts"]}
        assert "revised_direction" in types
        assert "audit" in types


# ---------------------------------------------------------------------------
# Intent retry loop
# ---------------------------------------------------------------------------


class TestIntentRetry:

    def test_rejected_then_approved(self, state):
        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=False)),
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=True)),
            ("checklist",    lambda cwd, **kw: _write_checklist(cwd)),
            ("subagent",     lambda **kw: _write_subagent(**kw)),
            ("llm3",         lambda cwd, **kw: _write_llm3(cwd, items=[])),
        ])
        result = revise_step_v2(state, call_llm=llm)
        assert result["last_result"] == "REVISED"

    def test_three_rejections_terminate(self, state):
        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=False)),
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=False)),
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=False)),
        ])
        result = revise_step_v2(state, call_llm=llm)
        assert result["last_result"] == "TERMINATE"
        assert "INTENT_RETRY_EXHAUSTED" in result["last_reason"]

    def test_intent_terminate_signal(self, state):
        # LLM1 voluntarily writes TERMINATE → orchestrator stops
        llm = ScriptedLLM([
            ("intent", lambda cwd, **kw: _write_intent(cwd, terminate=True)),
        ])
        result = revise_step_v2(state, call_llm=llm)
        assert result["last_result"] == "TERMINATE"
        assert "INTENT_GAVE_UP" in result["last_reason"]


# ---------------------------------------------------------------------------
# Checklist retry via UNIMPLEMENTABLE_CHECKLIST
# ---------------------------------------------------------------------------


class TestUnimplementableChecklist:

    def test_unimplementable_then_new_checklist_succeeds(self, state):
        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=True)),
            # first checklist
            ("checklist",    lambda cwd, **kw: _write_checklist(cwd)),
            # subagent reports unimplementable
            ("subagent", lambda **kw: _write_subagent(
                completion_yaml=_UNIMPLEMENTABLE_COMPLETION_YAML, **kw,
            )),
            # second checklist
            ("checklist",    lambda cwd, **kw: _write_checklist(cwd)),
            # subagent now succeeds
            ("subagent",     lambda **kw: _write_subagent(**kw)),
            ("llm3",         lambda cwd, **kw: _write_llm3(cwd, items=[])),
        ])
        result = revise_step_v2(state, call_llm=llm)
        assert result["last_result"] == "REVISED"


# ---------------------------------------------------------------------------
# Subagent retry via IMPLEMENTATION_FAILED
# ---------------------------------------------------------------------------


class TestImplementationFailed:

    def test_audit_fail_then_pass(self, state):
        # first subagent attempt: writes BASE_PY (no change) → deterministic FAIL
        # second subagent attempt: writes correct change → PASS
        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=True)),
            ("checklist",    lambda cwd, **kw: _write_checklist(cwd)),
            # attempt 0: write OLD .py (stoploss unchanged) but report completed:false to avoid dishonest streak
            ("subagent", lambda **kw: _write_subagent(
                new_py=_BASE_PY, completion_yaml=_INCOMPLETE_COMPLETION_YAML, **kw,
            )),
            # attempt 1: write changed .py + honest report
            ("subagent",     lambda **kw: _write_subagent(**kw)),
            ("llm3",         lambda cwd, **kw: _write_llm3(cwd, items=[])),
        ])
        result = revise_step_v2(state, call_llm=llm)
        assert result["last_result"] == "REVISED"


# ---------------------------------------------------------------------------
# Subagent dishonest streak
# ---------------------------------------------------------------------------


class TestDishonestStreak:

    @pytest.mark.skip(
        reason="config-driven 架構下 param items 不可能 dishonest（orchestrator 直接寫對）；"
               "重新設計需要 logic-items checklist + subagent 對 logic 撒謊，待 Step 7 e2e 後重寫"
    )
    def test_two_consecutive_dishonest_terminates(self, state):
        # attempt 0: subagent writes BASE_PY (stoploss unchanged) but reports completed:true → lie
        #   → audit deterministic FAIL on M1, short-circuits LLM3 (no llm3 call)
        #   → dishonest_streak = 1
        # attempt 1: same lie
        #   → dishonest_streak = 2 → TERMINATE
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
        result = revise_step_v2(state, call_llm=llm)
        assert result["last_result"] == "TERMINATE"
        assert "SUBAGENT_DISHONEST" in result["last_reason"]


# ---------------------------------------------------------------------------
# LLM unavailable
# ---------------------------------------------------------------------------


class TestLLMUnavailable:

    def test_intent_subprocess_failure_terminates(self, state):
        def _failing_call(prompt, cwd=None):
            raise RuntimeError("freqtrade LLM target not configured")
        result = revise_step_v2(state, call_llm=_failing_call)
        assert result["last_result"] == "TERMINATE"
        assert "STAGE_A_LLM_FAILED" in result["last_reason"]

    def test_no_rule_based_fallback(self, state):
        # confirm the v1 fallback (stoploss tighten) is NOT silently triggered
        def _failing_call(prompt, cwd=None):
            raise RuntimeError("LLM gone")
        result = revise_step_v2(state, call_llm=_failing_call)
        # plan should not be silently mutated
        assert "stoploss" not in (result.get("implementation_plan") or {}) or \
               result.get("implementation_plan", {}).get("stoploss") == state["implementation_plan"]["stoploss"]
        assert result["last_result"] == "TERMINATE"


# ---------------------------------------------------------------------------
# Staging preservation on TERMINATE
# ---------------------------------------------------------------------------


class TestStagingPreservation:

    def test_terminate_keeps_staging_dir(self, state):
        import app.freqtrade.steps.revise.v2 as v2_mod
        artifacts_dir = v2_mod.ARTIFACTS_DIR
        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=False)),
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=False)),
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=False)),
        ])
        result = revise_step_v2(state, call_llm=llm)
        assert result["last_result"] == "TERMINATE"
        staging = artifacts_dir / ".staging" / "v1"
        assert staging.exists()
        # at least one intent attempt persisted
        assert (staging / "intent_attempt_0.md").exists()
        # v{N}_audit.md produced
        assert (artifacts_dir / "v1_audit.md").exists()
        # NOT promoted: v1/ should not contain the strategy
        promoted = artifacts_dir / "strategies" / "v1"
        assert not promoted.exists() or not list(promoted.glob("*.py"))

    def test_approved_does_not_alter_v0(self, state):
        # Config-driven note: a param-only revise leaves the strategy .py
        # untouched — the new stoploss value lives in config.json, not the .py.
        # So v1's .py is byte-identical to v0; the "change" is in
        # plan.config_overrides.
        import app.freqtrade.steps.revise.v2 as v2_mod
        artifacts_dir = v2_mod.ARTIFACTS_DIR
        v0_py = artifacts_dir / "strategies" / "v0" / "FooStrategy.py"
        original_v0 = v0_py.read_text(encoding="utf-8")

        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=True)),
            ("checklist",    lambda cwd, **kw: _write_checklist(cwd)),
        ])
        result = revise_step_v2(state, call_llm=llm)
        assert result["last_result"] == "REVISED"
        # v0 untouched
        assert v0_py.read_text(encoding="utf-8") == original_v0
        # v1 created
        v1_py = artifacts_dir / "strategies" / "v1" / "FooStrategy.py"
        assert v1_py.exists()
        # In the new architecture v1 .py is byte-identical to v0 (param-only revise)
        assert v1_py.read_text(encoding="utf-8") == original_v0
        # The actual change is in plan.config_overrides
        assert result["implementation_plan"]["config_overrides"]["stoploss"] == -0.03


# ---------------------------------------------------------------------------
# Persistence of per-attempt YAMLs
# ---------------------------------------------------------------------------


class TestArtifactPersistence:

    def test_checklist_and_audit_files_kept_param_only(self, state):
        # In the config-driven architecture, a param-only checklist:
        # - generates checklist_attempt_0.yaml and audit_report_attempt_0.yaml
        # - skips the subagent (no logic items) so no subagent IO directory
        # - completion_report_attempt_0.yaml is the merged orchestrator view
        import app.freqtrade.steps.revise.v2 as v2_mod
        artifacts_dir = v2_mod.ARTIFACTS_DIR
        llm = ScriptedLLM([
            ("intent",       lambda cwd, **kw: _write_intent(cwd)),
            ("intent_audit", lambda cwd, **kw: _write_intent_audit(cwd, approved=True)),
            ("checklist",    lambda cwd, **kw: _write_checklist(cwd)),
        ])
        result = revise_step_v2(state, call_llm=llm)
        assert result["last_result"] == "REVISED"
        staging = artifacts_dir / ".staging" / "v1"
        assert (staging / "checklist_attempt_0.yaml").exists()
        assert (staging / "completion_report_attempt_0.yaml").exists()
        assert (staging / "audit_report_attempt_0.yaml").exists()

    @pytest.mark.skip(
        reason="config-driven 架構下 param-only checklist 不觸發 subagent，"
               "因此 IMPLEMENTATION_FAILED + subagent_retry path 需以 logic-items 構造，"
               "待 Step 7 e2e 後重寫"
    )
    def test_audit_report_attempt_files_kept(self, state):
        ...
