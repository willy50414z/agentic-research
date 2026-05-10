"""
tests/test_audit.py

Coverage for app/freqtrade/audit.py and the YAML schemas in app/freqtrade/checklist.py.

Tests are unit-level and mock the LLM call. They exercise:
  - deterministic_check_param across class_attr / hyperopt_param / dict_value
  - deterministic_check_invariants (timeframe, class name, order_types, look-ahead)
  - check_unauthorized_changes (positive + negative cases)
  - compute_subagent_self_report_consistent (deterministic + LLM3 + invariants)
  - LLM3 prompt input isolation (rationale / intent / last_reason absent)
  - INSUFFICIENT → CHECKLIST_AMBIGUOUS routing
  - dishonest_attempt continuity rules (including reset on checklist change)
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from app.freqtrade.audit import (
    check_unauthorized_changes,
    compute_subagent_self_report_consistent,
    deterministic_check_invariants,
    deterministic_check_param,
    derive_authorized_targets,
    llm3_audit_logic,
    run_audit,
    write_audit_report,
)
from app.freqtrade.checklist import (
    CHECKLIST_AMBIGUOUS_TAG,
    UNAUTHORIZED_CHANGE_ID,
    Checklist,
    CheckResult,
    ChecklistItem,
    CompletionItem,
    CompletionReport,
    ItemType,
    LogicTarget,
    Overall,
    ParamKind,
    ParamTarget,
    Verdict,
    parse_audit_report,
    parse_checklist,
    parse_completion_report,
)


# ---------------------------------------------------------------------------
# Fixtures: minimal valid strategy .py templates
# ---------------------------------------------------------------------------


_BASE_PY = textwrap.dedent('''
    """module docstring"""
    from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
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


def _strategy_with(**replacements: str) -> str:
    """Build a strategy .py from _BASE_PY, replacing literal substrings."""
    src = _BASE_PY
    for old, new in replacements.items():
        src = src.replace(old, new)
    return src


def _write(tmp_path: Path, name: str, src: str) -> Path:
    p = tmp_path / name
    p.write_text(src, encoding="utf-8")
    return p


def _checklist_with_items(items: list[ChecklistItem]) -> Checklist:
    return Checklist(
        iteration=1,
        based_on_intent="v1_revised_direction.md",
        created_by="llm2",
        locked=True,
        invariants=[
            "timeframe_unchanged", "class_name_unchanged",
            "order_types_four_keys", "no_lookahead_pattern",
        ],
        items=items,
    )


# ---------------------------------------------------------------------------
# 1.10 deterministic_check_param  (PASS/FAIL across kinds)
# ---------------------------------------------------------------------------


class TestDeterministicCheckParam:

    def test_class_attr_pass(self, tmp_path):
        new_py = _strategy_with(**{"stoploss = -0.05": "stoploss = -0.03"})
        new = _write(tmp_path, "new.py", new_py)
        item = ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.CLASS_ATTR, name="stoploss"),
            rationale="…", from_value=-0.05, to_value=-0.03,
        )
        result = deterministic_check_param(item, new)
        assert result.verdict == Verdict.PASS
        assert "stoploss" in result.evidence

    def test_class_attr_fail(self, tmp_path):
        new = _write(tmp_path, "new.py", _BASE_PY)  # stoploss still -0.05
        item = ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.CLASS_ATTR, name="stoploss"),
            rationale="…", from_value=-0.05, to_value=-0.03,
        )
        result = deterministic_check_param(item, new)
        assert result.verdict == Verdict.FAIL
        assert "-0.05" in result.evidence

    def test_hyperopt_param_default_pass(self, tmp_path):
        new_py = _strategy_with(
            **{"rsi_period = IntParameter(10, 20, default=14, space=\"buy\", optimize=True)":
               "rsi_period = IntParameter(10, 20, default=10, space=\"buy\", optimize=True)"}
        )
        new = _write(tmp_path, "new.py", new_py)
        item = ChecklistItem(
            id="M2", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.HYPEROPT_PARAM, name="rsi_period", field="default"),
            rationale="…", from_value=14, to_value=10,
        )
        result = deterministic_check_param(item, new)
        assert result.verdict == Verdict.PASS

    def test_hyperopt_param_default_fail(self, tmp_path):
        new = _write(tmp_path, "new.py", _BASE_PY)  # default=14 unchanged
        item = ChecklistItem(
            id="M2", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.HYPEROPT_PARAM, name="rsi_period", field="default"),
            rationale="…", from_value=14, to_value=10,
        )
        result = deterministic_check_param(item, new)
        assert result.verdict == Verdict.FAIL

    def test_dict_value_pass(self, tmp_path):
        new_py = _strategy_with(**{'minimal_roi = {"0": 0.10}': 'minimal_roi = {"0": 0.05}'})
        new = _write(tmp_path, "new.py", new_py)
        item = ChecklistItem(
            id="M5", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.DICT_VALUE, name="minimal_roi", path='minimal_roi."0"'),
            rationale="…", from_value=0.10, to_value=0.05,
        )
        result = deterministic_check_param(item, new)
        assert result.verdict == Verdict.PASS

    def test_dict_value_fail(self, tmp_path):
        new = _write(tmp_path, "new.py", _BASE_PY)
        item = ChecklistItem(
            id="M5", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.DICT_VALUE, name="minimal_roi", path='minimal_roi."0"'),
            rationale="…", from_value=0.10, to_value=0.05,
        )
        result = deterministic_check_param(item, new)
        assert result.verdict == Verdict.FAIL

    def test_class_attr_missing(self, tmp_path):
        new = _write(tmp_path, "new.py", _BASE_PY)
        item = ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.CLASS_ATTR, name="nonexistent_attr"),
            rationale="…", from_value=1, to_value=2,
        )
        result = deterministic_check_param(item, new)
        assert result.verdict == Verdict.FAIL


# ---------------------------------------------------------------------------
# 1.11 deterministic_check_invariants
# ---------------------------------------------------------------------------


class TestInvariants:

    INVARIANTS = [
        "timeframe_unchanged", "class_name_unchanged",
        "order_types_four_keys", "no_lookahead_pattern",
    ]

    def test_all_pass(self):
        results = deterministic_check_invariants(self.INVARIANTS, _BASE_PY, _BASE_PY)
        assert all(r.verdict == Verdict.PASS for r in results)
        # invariant results: subagent_self_report_consistent must be None per spec
        assert all(r.subagent_self_report_consistent is None for r in results)

    def test_timeframe_changed(self):
        new_py = _strategy_with(**{'timeframe = "4h"': 'timeframe = "1h"'})
        results = deterministic_check_invariants(self.INVARIANTS, _BASE_PY, new_py)
        tf = next(r for r in results if r.id == "timeframe_unchanged")
        assert tf.verdict == Verdict.FAIL

    def test_class_name_changed(self):
        new_py = _strategy_with(**{"class FooStrategy": "class BarStrategy"})
        results = deterministic_check_invariants(self.INVARIANTS, _BASE_PY, new_py)
        cn = next(r for r in results if r.id == "class_name_unchanged")
        assert cn.verdict == Verdict.FAIL

    def test_order_types_missing_key(self):
        new_py = _strategy_with(**{
            '"stoploss_on_exchange": False,\n    }': '}',
        })
        # sanity: the replacement actually changed the source
        assert "stoploss_on_exchange" not in new_py
        results = deterministic_check_invariants(self.INVARIANTS, _BASE_PY, new_py)
        ot = next(r for r in results if r.id == "order_types_four_keys")
        assert ot.verdict == Verdict.FAIL
        assert "stoploss_on_exchange" in ot.evidence

    def test_lookahead_detected(self):
        new_py = _strategy_with(**{
            'dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)':
                'dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)\n'
                '        dataframe["future"] = dataframe["close"].shift(-1)',
        })
        results = deterministic_check_invariants(self.INVARIANTS, _BASE_PY, new_py)
        la = next(r for r in results if r.id == "no_lookahead_pattern")
        assert la.verdict == Verdict.FAIL


# ---------------------------------------------------------------------------
# 1.19–1.22 check_unauthorized_changes
# ---------------------------------------------------------------------------


class TestUnauthorizedChanges:

    def _checklist(self, *items):
        return _checklist_with_items(list(items))

    def test_unauthorized_method_modified_fails(self):
        # checklist authorizes only populate_indicators
        cl = self._checklist(ChecklistItem(
            id="M3", type=ItemType.LOGIC,
            target=LogicTarget(function="populate_indicators"),
            rationale="…", expected_signals=["呼叫 ta.EMA"],
        ))
        new_py = _strategy_with(**{
            'dataframe.loc[(dataframe["rsi"] > 70), "exit_long"] = 1':
                'dataframe.loc[(dataframe["rsi"] > 65), "exit_long"] = 1',
        })
        result = check_unauthorized_changes(_BASE_PY, new_py, cl)
        assert result is not None
        assert result.id == UNAUTHORIZED_CHANGE_ID
        assert result.verdict == Verdict.FAIL
        assert "populate_exit_trend" in result.evidence

    def test_authorized_method_change_pass(self):
        cl = self._checklist(ChecklistItem(
            id="M3", type=ItemType.LOGIC,
            target=LogicTarget(function="populate_entry_trend"),
            rationale="…", expected_signals=["entry condition"],
        ))
        new_py = _strategy_with(**{
            'dataframe.loc[(dataframe["rsi"] < 30), "enter_long"] = 1':
                'dataframe.loc[(dataframe["rsi"] < 25), "enter_long"] = 1',
        })
        result = check_unauthorized_changes(_BASE_PY, new_py, cl)
        assert result is None  # no unauthorized change

    def test_pure_comment_change_no_trigger(self):
        cl = self._checklist(ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.CLASS_ATTR, name="stoploss"),
            rationale="…", from_value=-0.05, to_value=-0.05,
        ))
        new_py = _strategy_with(**{
            'dataframe.loc[(dataframe["rsi"] > 70), "exit_long"] = 1':
                'dataframe.loc[(dataframe["rsi"] > 70), "exit_long"] = 1  # noqa',
        })
        result = check_unauthorized_changes(_BASE_PY, new_py, cl)
        assert result is None

    def test_new_import_for_logic_item_no_trigger(self):
        # add a new import; checklist authorizes populate_indicators
        cl = self._checklist(ChecklistItem(
            id="M3", type=ItemType.LOGIC,
            target=LogicTarget(function="populate_indicators"),
            rationale="…", expected_signals=["EMA20"],
        ))
        new_py = _BASE_PY.replace(
            "import talib.abstract as ta",
            "import talib.abstract as ta\nimport numpy as np",
        )
        # also modify populate_indicators (authorized) so the file legitimately differs
        new_py = new_py.replace(
            'dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)',
            'dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)\n'
            '        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)',
        )
        result = check_unauthorized_changes(_BASE_PY, new_py, cl)
        assert result is None  # imports + authorized function body — both OK

    def test_class_docstring_modified_fails(self):
        cl = self._checklist(ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.CLASS_ATTR, name="stoploss"),
            rationale="…", from_value=-0.05, to_value=-0.03,
        ))
        new_py = _strategy_with(**{'"""class docstring"""': '"""new class docstring"""'})
        # also change stoploss to be authorized
        new_py = new_py.replace("stoploss = -0.05", "stoploss = -0.03")
        result = check_unauthorized_changes(_BASE_PY, new_py, cl)
        assert result is not None
        assert "docstring" in result.evidence

    def test_method_docstring_change_fails(self):
        cl = self._checklist(ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.CLASS_ATTR, name="stoploss"),
            rationale="…", from_value=-0.05, to_value=-0.03,
        ))
        # add a docstring to populate_exit_trend (which is NOT in the authorized set)
        new_py = _BASE_PY.replace(
            "def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:\n"
            "        dataframe.loc[(dataframe[\"rsi\"] > 70), \"exit_long\"] = 1",
            "def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:\n"
            '        """new docstring"""\n'
            "        dataframe.loc[(dataframe[\"rsi\"] > 70), \"exit_long\"] = 1",
        )
        new_py = new_py.replace("stoploss = -0.05", "stoploss = -0.03")
        result = check_unauthorized_changes(_BASE_PY, new_py, cl)
        assert result is not None
        assert "docstring" in result.evidence


# ---------------------------------------------------------------------------
# 1.15 / 1.16 compute_subagent_self_report_consistent
# ---------------------------------------------------------------------------


def _completion(*, items: list[CompletionItem], unimplementable: list[str] | None = None) -> CompletionReport:
    return CompletionReport(
        iteration=1, attempt=0, items=items,
        unimplementable_items=unimplementable or [],
    )


class TestSelfReportConsistency:

    def test_param_fail_reported_complete_is_dishonest(self):
        cl = _checklist_with_items([ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.CLASS_ATTR, name="stoploss"),
            rationale="…", from_value=-0.05, to_value=-0.03,
        )])
        det = [CheckResult(id="M1", verdict=Verdict.FAIL, evidence="…")]
        report = _completion(items=[CompletionItem(id="M1", completed=True, location="x.py:1")])
        det, _ = compute_subagent_self_report_consistent(det, [], report, cl)
        assert det[0].subagent_self_report_consistent is False  # dishonest

    def test_param_pass_reported_complete_is_honest(self):
        cl = _checklist_with_items([ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.CLASS_ATTR, name="stoploss"),
            rationale="…", from_value=-0.05, to_value=-0.03,
        )])
        det = [CheckResult(id="M1", verdict=Verdict.PASS, evidence="…")]
        report = _completion(items=[CompletionItem(id="M1", completed=True, location="x.py:1")])
        det, _ = compute_subagent_self_report_consistent(det, [], report, cl)
        assert det[0].subagent_self_report_consistent is True

    def test_invariant_fail_marked_null(self):
        cl = _checklist_with_items([ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.CLASS_ATTR, name="stoploss"),
            rationale="…", from_value=-0.05, to_value=-0.03,
        )])
        det = [CheckResult(id="timeframe_unchanged", verdict=Verdict.FAIL, evidence="…")]
        report = _completion(items=[CompletionItem(id="M1", completed=True, location="x.py:1")])
        det, _ = compute_subagent_self_report_consistent(det, [], report, cl)
        # invariant has no matching checklist id → None
        assert det[0].subagent_self_report_consistent is None

    def test_unauthorized_change_marked_null(self):
        cl = _checklist_with_items([ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.CLASS_ATTR, name="stoploss"),
            rationale="…", from_value=-0.05, to_value=-0.03,
        )])
        det = [CheckResult(id=UNAUTHORIZED_CHANGE_ID, verdict=Verdict.FAIL, evidence="…")]
        report = _completion(items=[CompletionItem(id="M1", completed=True, location="x.py:1")])
        det, _ = compute_subagent_self_report_consistent(det, [], report, cl)
        assert det[0].subagent_self_report_consistent is None

    def test_llm3_insufficient_reported_complete_is_dishonest(self):
        cl = _checklist_with_items([ChecklistItem(
            id="M3", type=ItemType.LOGIC,
            target=LogicTarget(function="populate_indicators"),
            rationale="…", expected_signals=["x"],
        )])
        llm = [CheckResult(id="M3", verdict=Verdict.INSUFFICIENT, evidence="ambiguous")]
        report = _completion(items=[CompletionItem(id="M3", completed=True, location="x.py:1")])
        _, llm = compute_subagent_self_report_consistent([], llm, report, cl)
        assert llm[0].subagent_self_report_consistent is False

    def test_subagent_reports_not_completed_not_dishonest(self):
        # subagent says "I didn't complete this", so failing audit is not lying
        cl = _checklist_with_items([ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(kind=ParamKind.CLASS_ATTR, name="stoploss"),
            rationale="…", from_value=-0.05, to_value=-0.03,
        )])
        det = [CheckResult(id="M1", verdict=Verdict.FAIL, evidence="…")]
        report = _completion(
            items=[CompletionItem(id="M1", completed=False, blocking_reason="impossible")],
            unimplementable=["M1"],
        )
        det, _ = compute_subagent_self_report_consistent(det, [], report, cl)
        assert det[0].subagent_self_report_consistent is True


# ---------------------------------------------------------------------------
# 1.13 LLM3 prompt input isolation
# ---------------------------------------------------------------------------


_LLM3_PROMPT_FIXTURE = """
TASK: audit logic items
ITEMS={ITEMS}
OLD_PY={OLD_PY}
NEW_PY={NEW_PY}
COMPLETION_REPORT={COMPLETION_REPORT}
OUTPUT_DIR={OUTPUT_DIR}
"""


class TestLLM3InputIsolation:

    def test_prompt_contains_no_rationale_intent_or_last_reason(self, tmp_path, monkeypatch):
        # write the LLM3 prompt template in the location audit.py expects
        prompts_dir = tmp_path / "prompts" / "freqtrade"
        prompts_dir.mkdir(parents=True)
        (prompts_dir / "revise_audit.txt").write_text(_LLM3_PROMPT_FIXTURE, encoding="utf-8")
        monkeypatch.setattr(
            "app.freqtrade.audit._load_llm3_prompt",
            lambda: _LLM3_PROMPT_FIXTURE,
        )

        captured: dict[str, str] = {}

        def _fake_call(prompt: str, cwd: str | None = None) -> str:
            captured["prompt"] = prompt
            # write a valid llm3_audit_result.yaml
            result_path = Path(cwd) / "llm3_audit_result.yaml"
            result_path.write_text(
                "items:\n  - id: M3\n    verdict: PASS\n    evidence: looks ok\n",
                encoding="utf-8",
            )
            return ""

        item = ChecklistItem(
            id="M3", type=ItemType.LOGIC,
            target=LogicTarget(function="populate_indicators"),
            rationale="DO_NOT_LEAK_RATIONALE_TOKEN",
            expected_signals=["呼叫 ta.EMA"],
        )
        report = _completion(items=[CompletionItem(id="M3", completed=True, location="x.py:1")])
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        results = llm3_audit_logic(
            [item], _BASE_PY, _BASE_PY, report, str(out_dir), call_llm=_fake_call,
        )
        assert results[0].verdict == Verdict.PASS
        prompt = captured["prompt"]
        # rationale must NOT appear in prompt
        assert "DO_NOT_LEAK_RATIONALE_TOKEN" not in prompt
        # expected_signals SHOULD appear (they are LLM3-permitted)
        assert "呼叫 ta.EMA" in prompt


# ---------------------------------------------------------------------------
# 1.12 INSUFFICIENT → CHECKLIST_AMBIGUOUS routing
# ---------------------------------------------------------------------------


class TestInsufficientRouting:

    def test_llm3_insufficient_classifies_as_checklist_ambiguous(self, tmp_path, monkeypatch):
        # all deterministic clean, LLM3 returns INSUFFICIENT for one item
        cl = _checklist_with_items([ChecklistItem(
            id="M3", type=ItemType.LOGIC,
            target=LogicTarget(function="populate_indicators"),
            rationale="…", expected_signals=["whatever"],
        )])
        old = tmp_path / "old.py"; old.write_text(_BASE_PY, encoding="utf-8")
        new = tmp_path / "new.py"; new.write_text(_BASE_PY, encoding="utf-8")
        report = _completion(items=[CompletionItem(id="M3", completed=True, location="x.py:1")])
        out_dir = tmp_path / "out"; out_dir.mkdir()

        monkeypatch.setattr(
            "app.freqtrade.audit._load_llm3_prompt",
            lambda: _LLM3_PROMPT_FIXTURE,
        )

        def _fake_call(prompt: str, cwd: str | None = None) -> str:
            (Path(cwd) / "llm3_audit_result.yaml").write_text(
                "items:\n  - id: M3\n    verdict: INSUFFICIENT\n    evidence: ambiguous\n",
                encoding="utf-8",
            )
            return ""

        report_obj = run_audit(cl, new, old, report, str(out_dir), call_llm=_fake_call)
        assert report_obj.overall == Overall.REJECTED
        assert CHECKLIST_AMBIGUOUS_TAG in (report_obj.reject_summary or "")
        assert report_obj.should_route_to_stage_c() is True


# ---------------------------------------------------------------------------
# 1.16 dishonest_attempt continuity helpers
#    The orchestrator handles continuity, but AuditReport.has_dishonest_attempt
#    is the per-attempt detector that the orchestrator consumes.
# ---------------------------------------------------------------------------


class TestDishonestAttemptDetection:

    def test_attempt_with_consistent_false_is_dishonest(self):
        report = parse_audit_report(textwrap.dedent("""
            iteration: 1
            attempt: 0
            deterministic_results:
              - id: M1
                verdict: FAIL
                evidence: stoploss not changed
                subagent_self_report_consistent: false
            llm3_results: []
            overall: REJECTED
            reject_summary: deterministic FAIL
        """))
        assert report.has_dishonest_attempt() is True

    def test_attempt_with_only_invariant_fail_is_not_dishonest(self):
        report = parse_audit_report(textwrap.dedent("""
            iteration: 1
            attempt: 0
            deterministic_results:
              - id: timeframe_unchanged
                verdict: FAIL
                evidence: timeframe changed
                subagent_self_report_consistent: null
            llm3_results: []
            overall: REJECTED
            reject_summary: invariant FAIL
        """))
        assert report.has_dishonest_attempt() is False


# ---------------------------------------------------------------------------
# write_audit_report (1.9a) round-trip
# ---------------------------------------------------------------------------


class TestWriteAuditReport:

    def test_write_and_reparse(self, tmp_path):
        report = parse_audit_report(textwrap.dedent("""
            iteration: 2
            attempt: 1
            deterministic_results:
              - id: M1
                verdict: PASS
                evidence: ok
                subagent_self_report_consistent: true
            llm3_results:
              - id: M3
                verdict: FAIL
                evidence: bad
                subagent_self_report_consistent: false
            overall: REJECTED
            reject_summary: LLM3 FAIL
        """))
        path = write_audit_report(report, tmp_path / "audit_report_attempt_1.yaml")
        assert path.exists()
        roundtrip = parse_audit_report(path.read_text(encoding="utf-8"))
        assert roundtrip.iteration == 2
        assert roundtrip.attempt == 1
        assert roundtrip.overall == Overall.REJECTED
        assert roundtrip.deterministic_results[0].verdict == Verdict.PASS
        assert roundtrip.llm3_results[0].verdict == Verdict.FAIL


# ---------------------------------------------------------------------------
# parse_checklist sanity (covers 1.4 schema validation)
# ---------------------------------------------------------------------------


class TestParseChecklist:

    def _ok_yaml(self, **overrides) -> str:
        base = {
            "iteration": 1,
            "based_on_intent": "v1_intent.md",
            "created_by": "llm2",
            "locked": True,
            "invariants": [
                "timeframe_unchanged", "class_name_unchanged",
                "order_types_four_keys", "no_lookahead_pattern",
            ],
            "items": [
                {
                    "id": "M1", "type": "param",
                    "target": {"kind": "class_attr", "name": "stoploss"},
                    "from": -0.05, "to": -0.03,
                    "rationale": "drawdown reduction",
                },
            ],
        }
        base.update(overrides)
        import yaml as _yaml
        return _yaml.safe_dump(base, sort_keys=False)

    def test_valid(self):
        cl = parse_checklist(self._ok_yaml())
        assert cl.iteration == 1 and cl.locked is True
        assert cl.items[0].id == "M1"

    def test_locked_false_rejected(self):
        with pytest.raises(Exception):
            parse_checklist(self._ok_yaml(locked=False))

    def test_missing_invariant_rejected(self):
        with pytest.raises(Exception):
            parse_checklist(self._ok_yaml(invariants=["timeframe_unchanged"]))

    def test_param_missing_from_rejected(self):
        import yaml as _yaml
        bad = _yaml.safe_load(self._ok_yaml())
        del bad["items"][0]["from"]
        with pytest.raises(Exception):
            parse_checklist(_yaml.safe_dump(bad))
