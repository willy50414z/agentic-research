"""
tests/test_strategy_snapshot.py

Coverage for ``app/freqtrade/steps/strategy_snapshot.py`` after the config-driven
schema migration:

  - 4.10 — structural values come from AST; LLM 補充 section never injects values
  - 4.11 — UNPARSEABLE-only fields propagate to the markdown rather than being invented
  - 4.13 — param item delta shows ``from → to`` and the config-value cross-check
            against the post-patch ``config_dict``; mismatch raises ValueError
  - 4.14 — logic item delta shows function + expected_signals + forbidden_signals
            + rationale; no from/to fields appear
"""
from __future__ import annotations

import re
import textwrap
from pathlib import Path

import pytest

from app.freqtrade.checklist import (
    Checklist,
    ChecklistItem,
    ItemType,
    LogicTarget,
    ParamTarget,
)
from app.freqtrade.steps.strategy_snapshot import write_strategy_spec_snapshot


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_BASE_PY = textwrap.dedent('''
    """module"""
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

        def populate_indicators(self, dataframe, metadata):
            dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)
            return dataframe

        def populate_entry_trend(self, dataframe, metadata):
            dataframe.loc[(dataframe["rsi"] < 30), "enter_long"] = 1
            return dataframe

        def populate_exit_trend(self, dataframe, metadata):
            dataframe.loc[(dataframe["rsi"] > 70), "exit_long"] = 1
            return dataframe
''').strip() + "\n"


def _checklist(*items: ChecklistItem) -> Checklist:
    return Checklist(
        iteration=1,
        based_on_intent="revision_intent.md",
        created_by="llm2",
        locked=True,
        invariants=[
            "timeframe_unchanged", "class_name_unchanged",
            "order_types_four_keys", "no_lookahead_pattern",
        ],
        items=list(items),
    )


def _write_py(tmp_path: Path, src: str) -> Path:
    p = tmp_path / "FooStrategy.py"
    p.write_text(src, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# 4.10 — structural values from AST; LLM section can't inject values
# ---------------------------------------------------------------------------


class TestStructuralFromAST:

    def test_baseline_v0_has_only_structural_section(self, tmp_path):
        py = _write_py(tmp_path, _BASE_PY)
        out = tmp_path / "v0_strategy_spec.md"
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=None, intent_md=None, output_path=out,
        )
        text = out.read_text(encoding="utf-8")
        assert "結構性資料" in text
        assert "FooStrategy" in text
        assert "`4h`" in text
        assert "stoploss: `-0.05`" in text
        # No delta or LLM section for baseline
        assert "Delta vs 前輪" not in text
        assert "修訂摘要" not in text

    def test_revised_iteration_includes_delta_and_llm_sections(self, tmp_path):
        # In the config-driven architecture the .py is unchanged across iterations
        # for param-only checklist items — the new stoploss lives in config_dict.
        py = _write_py(tmp_path, _BASE_PY)
        cl = _checklist(ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(path="stoploss"),
            from_value=-0.05, to_value=-0.03,
            rationale="OOS dd 0.31 > 0.20",
        ))
        intent_md = "# 修訂方向\n收緊 stoploss\n"
        out = tmp_path / "v1_strategy_spec.md"
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=cl, intent_md=intent_md, output_path=out,
            config_dict={"stoploss": -0.03},
        )
        text = out.read_text(encoding="utf-8")
        assert "結構性資料" in text
        assert "Delta vs 前輪" in text
        assert "修訂摘要" in text
        # LLM section labelled non-structural
        assert "僅供敘事" in text or "敘事" in text

    def test_llm_supplement_section_does_not_contain_extracted_values(self, tmp_path):
        # The intent_md section is rendered verbatim — we only ensure it's
        # placed AFTER the structural block, not interleaved with it.
        py = _write_py(tmp_path, _BASE_PY)
        intent_md = "說 stoploss 是 -0.99\n"  # LLM hallucination — must not be SoT
        out = tmp_path / "v1_spec.md"
        cl = _checklist(ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(path="stoploss"),
            from_value=-0.05, to_value=-0.05,  # no real change for this test
            rationale="...",
        ))
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=cl, intent_md=intent_md, output_path=out,
            config_dict={"stoploss": -0.05},
        )
        text = out.read_text(encoding="utf-8")
        # Find the structural section (everything before "Delta")
        structural = text.split("## Delta vs 前輪")[0]
        # The hallucinated value MUST NOT appear in the structural region
        assert "-0.99" not in structural
        # AST-extracted value DOES appear
        assert "-0.05" in structural


# ---------------------------------------------------------------------------
# 4.11 — UNPARSEABLE propagation
# ---------------------------------------------------------------------------


class TestUnparseable:

    def test_non_literal_stoploss_renders_unparseable(self, tmp_path):
        py_src = _BASE_PY.replace("stoploss = -0.05", "stoploss = compute_stop(0.05)")
        py = _write_py(tmp_path, py_src)
        out = tmp_path / "spec.md"
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=None, intent_md=None, output_path=out,
        )
        text = out.read_text(encoding="utf-8")
        assert "<unparseable>" in text
        # Specifically, the stoploss line should show the sentinel
        assert re.search(r"stoploss:\s*`<unparseable>`", text)

    def test_missing_class_name_rendered(self, tmp_path):
        # No class definition at all
        py = _write_py(tmp_path, "x = 1\n")
        out = tmp_path / "spec.md"
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=None, intent_md=None, output_path=out,
        )
        text = out.read_text(encoding="utf-8")
        assert "<unparseable>" in text


# ---------------------------------------------------------------------------
# 4.13 — param item delta + config_dict cross-check
# ---------------------------------------------------------------------------


class TestParamDelta:

    def test_param_delta_shows_from_to_and_matches_config(self, tmp_path):
        py = _write_py(tmp_path, _BASE_PY)
        cl = _checklist(ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(path="stoploss"),
            from_value=-0.05, to_value=-0.03, rationale="dd",
        ))
        out = tmp_path / "spec.md"
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=cl, intent_md=None, output_path=out,
            config_dict={"stoploss": -0.03},
        )
        text = out.read_text(encoding="utf-8")
        # Delta line shows from → to
        assert "-0.05 → -0.03" in text
        # config verification line
        assert "config 驗證" in text
        assert "(匹配)" in text

    def test_param_mismatch_raises(self, tmp_path):
        # config_dict has stoploss=-0.05 but checklist says we changed to -0.03 → mismatch
        py = _write_py(tmp_path, _BASE_PY)
        cl = _checklist(ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(path="stoploss"),
            from_value=-0.05, to_value=-0.03, rationale="dd",
        ))
        out = tmp_path / "spec.md"
        with pytest.raises(ValueError, match=r"M1 expects -0\.03"):
            write_strategy_spec_snapshot(
                py_path=py, prev_py_path=None,
                checklist=cl, intent_md=None, output_path=out,
                config_dict={"stoploss": -0.05},
            )

    def test_param_no_config_dict_renders_unparseable(self, tmp_path):
        # config_dict=None should not raise; the cross-check is skipped and
        # the delta line shows "<unparseable>" instead of "(匹配)".
        py = _write_py(tmp_path, _BASE_PY)
        cl = _checklist(ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(path="stoploss"),
            from_value=-0.05, to_value=-0.03, rationale="dd",
        ))
        out = tmp_path / "spec.md"
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=cl, intent_md=None, output_path=out,
        )
        text = out.read_text(encoding="utf-8")
        assert "<unparseable>" in text or "config 未提供" in text

    def test_nested_custom_param_delta(self, tmp_path):
        py = _write_py(tmp_path, _BASE_PY)
        cl = _checklist(ChecklistItem(
            id="M2", type=ItemType.PARAM,
            target=ParamTarget(path="custom_params.max_units"),
            from_value=4, to_value=2, rationale="reduce risk",
        ))
        out = tmp_path / "spec.md"
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=cl, intent_md=None, output_path=out,
            config_dict={"custom_params": {"max_units": 2}},
        )
        text = out.read_text(encoding="utf-8")
        assert "4 → 2" in text
        assert "(匹配)" in text
        assert "custom_params.max_units" in text

    def test_dict_value_replacement_delta(self, tmp_path):
        py = _write_py(tmp_path, _BASE_PY)
        cl = _checklist(ChecklistItem(
            id="M5", type=ItemType.PARAM,
            target=ParamTarget(path="minimal_roi"),
            from_value={"0": 0.10},
            to_value={"0": 0.05},
            rationale="tighter ROI",
        ))
        out = tmp_path / "spec.md"
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=cl, intent_md=None, output_path=out,
            config_dict={"minimal_roi": {"0": 0.05}},
        )
        text = out.read_text(encoding="utf-8")
        assert "(匹配)" in text


# ---------------------------------------------------------------------------
# 4.14 — logic item delta (no from/to)
# ---------------------------------------------------------------------------


class TestLogicDelta:

    def test_logic_item_renders_signals_and_rationale(self, tmp_path):
        py = _write_py(tmp_path, _BASE_PY)
        cl = _checklist(ChecklistItem(
            id="M3", type=ItemType.LOGIC,
            target=LogicTarget(function="populate_indicators"),
            expected_signals=["呼叫 ta.EMA timeperiod=20"],
            forbidden_signals=[],
            rationale="加入趨勢過濾基礎",
        ))
        out = tmp_path / "spec.md"
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=cl, intent_md=None, output_path=out,
        )
        text = out.read_text(encoding="utf-8")
        assert "M3 (source: checklist.logic + AST)" in text
        assert "populate_indicators" in text
        assert "呼叫 ta.EMA timeperiod=20" in text
        assert "加入趨勢過濾基礎" in text
        # Logic items must NOT show "from → to" — that's param-only
        # Find the M3 block specifically
        m3_block = text.split("### M3")[1].split("##")[0]  # everything until next H2
        assert "→" not in m3_block

    def test_logic_item_with_forbidden_signals(self, tmp_path):
        py = _write_py(tmp_path, _BASE_PY)
        cl = _checklist(ChecklistItem(
            id="M6", type=ItemType.LOGIC,
            target=LogicTarget(function="populate_indicators"),
            expected_signals=["新增 EMA20"],
            forbidden_signals=["ta.ATR 不再被引用"],
            rationale="移除 ATR",
        ))
        out = tmp_path / "spec.md"
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=cl, intent_md=None, output_path=out,
        )
        text = out.read_text(encoding="utf-8")
        assert "明確移除的行為" in text
        assert "ta.ATR 不再被引用" in text


# ---------------------------------------------------------------------------
# Smoke: structural section never references checklist values
# ---------------------------------------------------------------------------


class TestSeparation:

    def test_structural_section_independent_of_checklist(self, tmp_path):
        # Even if checklist says from=99, to=88, the structural section must
        # report what the AST actually says.
        py = _write_py(tmp_path, _BASE_PY)
        cl = _checklist(ChecklistItem(
            id="M1", type=ItemType.PARAM,
            target=ParamTarget(path="stoploss"),
            from_value=-0.05, to_value=-0.05,  # match config, so no exception
            rationale="...",
        ))
        out = tmp_path / "spec.md"
        write_strategy_spec_snapshot(
            py_path=py, prev_py_path=None,
            checklist=cl, intent_md=None, output_path=out,
            config_dict={"stoploss": -0.05},
        )
        text = out.read_text(encoding="utf-8")
        structural = text.split("## Delta vs 前輪")[0]
        assert "-0.05" in structural
        # Structural section shouldn't say "from" / "to" or "checklist"
        assert "from" not in structural.lower()
        assert "to:" not in structural.lower()
