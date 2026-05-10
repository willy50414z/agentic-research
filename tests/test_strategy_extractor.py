"""
tests/test_strategy_extractor.py

Coverage for app/freqtrade/strategy_extractor.py — the deterministic AST
extractor that powers `v{N}_strategy_spec.md` snapshots.

Tests follow the same fixture pattern as tests/test_audit.py: a single
``_BASE_PY`` template plus targeted ``str.replace`` swaps so the dependent
§4 wiring tests can lean on the same shape.
"""
from __future__ import annotations

import textwrap

from app.freqtrade.strategy_extractor import (
    UNPARSEABLE,
    extract_class_name,
    extract_entry_conditions,
    extract_exit_conditions,
    extract_hyperopt_params,
    extract_minimal_roi,
    extract_stoploss,
    extract_timeframe,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


_BASE_PY = textwrap.dedent('''
    """module docstring"""
    from freqtrade.strategy import (
        IStrategy, IntParameter, DecimalParameter, CategoricalParameter, BooleanParameter,
    )
    import talib.abstract as ta
    from pandas import DataFrame


    class FooStrategy(IStrategy):
        """class docstring"""

        timeframe = "4h"
        stoploss = -0.05
        minimal_roi = {"0": 0.10, "60": 0.05}

        order_types = {
            "entry": "market",
            "exit": "market",
            "stoploss": "market",
            "stoploss_on_exchange": False,
        }

        rsi_period = IntParameter(10, 20, default=14, space="buy", optimize=True)
        rsi_buy = DecimalParameter(20.0, 40.0, default=30.0, decimals=1, space="buy")
        signal_kind = CategoricalParameter(["fast", "slow"], default="fast", space="buy")
        use_filter = BooleanParameter(default=True, space="buy")

        def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
            dataframe["rsi"] = ta.RSI(dataframe, timeperiod=self.rsi_period.value)
            dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
            return dataframe

        def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
            dataframe.loc[
                (dataframe["rsi"] < 30) & (dataframe["close"] > dataframe["ema20"]),
                "enter_long",
            ] = 1
            return dataframe

        def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
            dataframe.loc[(dataframe["rsi"] > 70), "exit_long"] = 1
            return dataframe
''').strip() + "\n"


def _strategy_with(**replacements: str) -> str:
    src = _BASE_PY
    for old, new in replacements.items():
        src = src.replace(old, new)
    return src


# ---------------------------------------------------------------------------
# extract_class_name
# ---------------------------------------------------------------------------


class TestExtractClassName:

    def test_standard(self):
        assert extract_class_name(_BASE_PY) == "FooStrategy"

    def test_no_class_returns_none(self):
        py = textwrap.dedent('''
            """just a module"""
            x = 1
            def f(): pass
        ''').strip() + "\n"
        assert extract_class_name(py) is None

    def test_syntax_error_returns_none(self):
        assert extract_class_name("class Foo(:::") is None


# ---------------------------------------------------------------------------
# extract_timeframe
# ---------------------------------------------------------------------------


class TestExtractTimeframe:

    def test_standard(self):
        assert extract_timeframe(_BASE_PY) == "4h"

    def test_alternate(self):
        py = _strategy_with(**{'timeframe = "4h"': 'timeframe = "1h"'})
        assert extract_timeframe(py) == "1h"

    def test_missing_attr(self):
        py = _strategy_with(**{'timeframe = "4h"\n': ""})
        assert extract_timeframe(py) == UNPARSEABLE

    def test_non_literal(self):
        # f-string is not a literal — ast.literal_eval will reject it
        py = _strategy_with(**{'timeframe = "4h"': 'timeframe = f"{base}h"'})
        assert extract_timeframe(py) == UNPARSEABLE


# ---------------------------------------------------------------------------
# extract_stoploss
# ---------------------------------------------------------------------------


class TestExtractStoploss:

    def test_standard(self):
        assert extract_stoploss(_BASE_PY) == -0.05

    def test_alternate(self):
        py = _strategy_with(**{"stoploss = -0.05": "stoploss = -0.03"})
        assert extract_stoploss(py) == -0.03

    def test_missing_returns_sentinel(self):
        py = _strategy_with(**{"stoploss = -0.05\n": ""})
        assert extract_stoploss(py) == UNPARSEABLE

    def test_non_literal_returns_sentinel(self):
        # function call in lieu of a literal
        py = _strategy_with(**{"stoploss = -0.05": "stoploss = compute_stop()"})
        assert extract_stoploss(py) == UNPARSEABLE


# ---------------------------------------------------------------------------
# extract_minimal_roi
# ---------------------------------------------------------------------------


class TestExtractMinimalRoi:

    def test_standard(self):
        roi = extract_minimal_roi(_BASE_PY)
        assert roi == {"0": 0.10, "60": 0.05}

    def test_missing_returns_sentinel(self):
        py = _strategy_with(**{'minimal_roi = {"0": 0.10, "60": 0.05}\n': ""})
        assert extract_minimal_roi(py) == UNPARSEABLE

    def test_non_dict_expression(self):
        py = _strategy_with(**{
            'minimal_roi = {"0": 0.10, "60": 0.05}': "minimal_roi = build_roi()",
        })
        assert extract_minimal_roi(py) == UNPARSEABLE

    def test_non_literal_value_in_dict(self):
        py = _strategy_with(**{
            'minimal_roi = {"0": 0.10, "60": 0.05}':
                'minimal_roi = {"0": some_var}',
        })
        assert extract_minimal_roi(py) == UNPARSEABLE


# ---------------------------------------------------------------------------
# extract_hyperopt_params
# ---------------------------------------------------------------------------


class TestExtractHyperoptParams:

    def test_int_parameter_positional_low_high(self):
        params = extract_hyperopt_params(_BASE_PY)
        assert "rsi_period" in params
        rsi = params["rsi_period"]
        assert rsi["kind"] == "IntParameter"
        assert rsi["default"] == 14
        assert rsi["low"] == 10
        assert rsi["high"] == 20
        # decimals not provided → sentinel
        assert rsi["decimals"] == UNPARSEABLE

    def test_decimal_parameter_with_decimals(self):
        params = extract_hyperopt_params(_BASE_PY)
        assert "rsi_buy" in params
        rsi_buy = params["rsi_buy"]
        assert rsi_buy["kind"] == "DecimalParameter"
        assert rsi_buy["default"] == 30.0
        assert rsi_buy["low"] == 20.0
        assert rsi_buy["high"] == 40.0
        assert rsi_buy["decimals"] == 1

    def test_categorical_parameter(self):
        params = extract_hyperopt_params(_BASE_PY)
        assert "signal_kind" in params
        sk = params["signal_kind"]
        assert sk["kind"] == "CategoricalParameter"
        assert sk["default"] == "fast"
        # CategoricalParameter has its choices list as first positional arg
        # (interpreted as `low` by the shared helper); high/decimals not provided.
        assert sk["low"] == ["fast", "slow"]
        assert sk["high"] == UNPARSEABLE
        assert sk["decimals"] == UNPARSEABLE

    def test_boolean_parameter(self):
        params = extract_hyperopt_params(_BASE_PY)
        assert "use_filter" in params
        bp = params["use_filter"]
        assert bp["kind"] == "BooleanParameter"
        assert bp["default"] is True

    def test_keyword_only_int_parameter(self):
        py = _strategy_with(**{
            "rsi_period = IntParameter(10, 20, default=14, space=\"buy\", optimize=True)":
                "rsi_period = IntParameter(low=12, high=22, default=15, space=\"buy\")",
        })
        params = extract_hyperopt_params(py)
        rsi = params["rsi_period"]
        assert rsi["low"] == 12
        assert rsi["high"] == 22
        assert rsi["default"] == 15

    def test_non_literal_default_falls_back_for_that_field(self):
        py = _strategy_with(**{
            "rsi_period = IntParameter(10, 20, default=14, space=\"buy\", optimize=True)":
                "rsi_period = IntParameter(10, 20, default=compute_default(), space=\"buy\")",
        })
        params = extract_hyperopt_params(py)
        rsi = params["rsi_period"]
        assert rsi["default"] == UNPARSEABLE
        # other fields still extracted
        assert rsi["low"] == 10
        assert rsi["high"] == 20

    def test_no_class_returns_empty(self):
        py = "x = 1\n"
        assert extract_hyperopt_params(py) == {}


# ---------------------------------------------------------------------------
# extract_entry_conditions / extract_exit_conditions
# ---------------------------------------------------------------------------


class TestExtractEntryConditions:

    def test_bitand_combination(self):
        # default _BASE_PY uses `&` (bitwise) joining two Compares
        rendered = extract_entry_conditions(_BASE_PY)
        assert rendered == 'rsi < 30 AND close > ema20'

    def test_simple_single_compare(self):
        py = _strategy_with(**{
            '        dataframe.loc[\n'
            '            (dataframe["rsi"] < 30) & (dataframe["close"] > dataframe["ema20"]),\n'
            '            "enter_long",\n'
            '        ] = 1':
                '        dataframe.loc[(dataframe["rsi"] < 25), "enter_long"] = 1',
        })
        assert extract_entry_conditions(py) == "rsi < 25"

    def test_bitor_combination(self):
        py = _strategy_with(**{
            '        dataframe.loc[\n'
            '            (dataframe["rsi"] < 30) & (dataframe["close"] > dataframe["ema20"]),\n'
            '            "enter_long",\n'
            '        ] = 1':
                '        dataframe.loc[\n'
                '            (dataframe["rsi"] < 30) | (dataframe["close"] > dataframe["ema20"]),\n'
                '            "enter_long",\n'
                '        ] = 1',
        })
        rendered = extract_entry_conditions(py)
        assert rendered == 'rsi < 30 OR close > ema20'

    def test_nested_bitand_bitor(self):
        py = _strategy_with(**{
            '        dataframe.loc[\n'
            '            (dataframe["rsi"] < 30) & (dataframe["close"] > dataframe["ema20"]),\n'
            '            "enter_long",\n'
            '        ] = 1':
                '        dataframe.loc[\n'
                '            ((dataframe["rsi"] < 30) | (dataframe["rsi"] > 70)) & (dataframe["close"] > dataframe["ema20"]),\n'
                '            "enter_long",\n'
                '        ] = 1',
        })
        rendered = extract_entry_conditions(py)
        # OR is parenthesised because it sits inside an AND.
        assert rendered == '(rsi < 30 OR rsi > 70) AND close > ema20'

    def test_missing_function_returns_sentinel(self):
        py = _strategy_with(**{
            '    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:\n'
            '        dataframe.loc[\n'
            '            (dataframe["rsi"] < 30) & (dataframe["close"] > dataframe["ema20"]),\n'
            '            "enter_long",\n'
            '        ] = 1\n'
            '        return dataframe\n':
                "",
        })
        assert extract_entry_conditions(py) == UNPARSEABLE

    def test_unrecognised_structure_returns_sentinel(self):
        py = _strategy_with(**{
            '        dataframe.loc[\n'
            '            (dataframe["rsi"] < 30) & (dataframe["close"] > dataframe["ema20"]),\n'
            '            "enter_long",\n'
            '        ] = 1':
                '        dataframe["enter_long"] = compute_signal(dataframe)',
        })
        assert extract_entry_conditions(py) == UNPARSEABLE


class TestExtractExitConditions:

    def test_simple(self):
        rendered = extract_exit_conditions(_BASE_PY)
        assert rendered == "rsi > 70"

    def test_bool_op_and(self):
        # Use `and` keyword (BoolOp) instead of bitwise — extractor supports both.
        py = _strategy_with(**{
            '        dataframe.loc[(dataframe["rsi"] > 70), "exit_long"] = 1':
                '        dataframe.loc[\n'
                '            (dataframe["rsi"] > 70) and (dataframe["close"] < dataframe["ema20"]),\n'
                '            "exit_long",\n'
                '        ] = 1',
        })
        rendered = extract_exit_conditions(py)
        assert rendered == "rsi > 70 AND close < ema20"

    def test_missing_function_returns_sentinel(self):
        py = _strategy_with(**{
            '    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:\n'
            '        dataframe.loc[(dataframe["rsi"] > 70), "exit_long"] = 1\n'
            '        return dataframe\n':
                "",
        })
        assert extract_exit_conditions(py) == UNPARSEABLE
